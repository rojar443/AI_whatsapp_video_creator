"""Footage pipeline (app.reel): quality filter, plan validation, director agent loop, rendering.
The vision model is a scripted fake, so no API calls are made."""
import json
import subprocess

import pytest

from app.reel import playbooks
from app.reel.analyze import QualityAnalyzer
from app.reel.ingest import FolderIngestor
from app.reel.llm import ToolResult, VisionLLM
from app.reel.models import Brief, ShotScore, brief_from_prompt
from app.reel.pipeline import build_pipeline
from app.reel.sample import FFmpegSceneSampler
from app.reel.select import DirectorSelector, HeuristicSelector
from app.reel.timeline import TimelineSelector, fit_timeline
from app.reel.tools import TOOL_SPECS, Session, validate_plan
from tests.conftest import requires_ffmpeg


def _ff(*args):
    subprocess.run(["ffmpeg", "-y", "-v", "error", *args], check=True)


@pytest.fixture(scope="module")
def clips(tmp_path_factory):
    d = tmp_path_factory.mktemp("clips")
    _ff("-f", "lavfi", "-i", "testsrc2=s=640x360:r=30:d=6", str(d / "a.mp4"))
    _ff("-f", "lavfi", "-i", "mandelbrot=s=360x640:r=30", "-t", "4", str(d / "b.mp4"))
    _ff("-f", "lavfi", "-i", "color=c=0x050505:s=640x360:d=3", str(d / "c_dark.mp4"))
    _ff("-f", "lavfi", "-i", "testsrc2=s=640x360:r=30:d=6", str(d / "d_dup.mp4"))
    _ff("-f", "lavfi", "-i", "cellauto=s=600x800", "-frames:v", "1", str(d / "e.jpg"))
    return d


def _session(clips, tmp_path, duration=8.0):
    assets = FolderIngestor().load(str(clips))
    shots = FFmpegSceneSampler().sample(assets, str(tmp_path))
    session = Session(Brief("calm travel reel", "travel", duration), assets, shots,
                      {s.id: ShotScore(s.id) for s in shots}, str(tmp_path))
    QualityAnalyzer().analyze(shots, session.scores, session.brief)
    return session


@requires_ffmpeg
def test_quality_filter_drops_dark_and_duplicates(clips, tmp_path):
    s = _session(clips, tmp_path)
    reasons = {sid: sc.dropped for sid, sc in s.scores.items() if sc.dropped}
    assert "too dark" in reasons.values()
    assert any(r.startswith("near-duplicate") for r in reasons.values())
    assert len(s.candidates()) >= 3
    assert all(sh.kind == "photo" or sh.shake_frame for sh in s.shots)


@requires_ffmpeg
def test_validate_plan_rejects_bad_ids_dropped_and_reuse(clips, tmp_path):
    s = _session(clips, tmp_path)
    dropped = next(sid for sid, sc in s.scores.items() if sc.dropped)
    ok = s.candidates()[0].id
    plan, errors = validate_plan(s, {"items": [{"shot_id": "S99"}, {"shot_id": dropped},
                                               {"shot_id": ok}, {"shot_id": ok}]}, "t")
    assert plan is None
    assert any("unknown" in e for e in errors) and any("dropped" in e for e in errors)
    assert any("twice" in e for e in errors)


class ScriptedDirector(VisionLLM):
    """Plays a fixed tool-call script against the real ToolBox, like a model would."""
    name = "fake"

    def __init__(self, bad_first=True):
        self.bad_first, self.calls = bad_first, []

    def analyze_images(self, prompt, images, schema):
        return {"shots": []}

    def run_tools(self, system, user, tools, handler, max_steps=8):
        assert {t["name"] for t in tools} == {t["name"] for t in TOOL_SPECS}
        shots = json.loads(handler("list_shots", {}).text)
        self.calls.append("list_shots")
        seen = handler("view_shots", {"ids": [x["id"] for x in shots]})
        assert seen.images and all(p.endswith(".jpg") for _, p in seen.images)
        if self.bad_first:
            r = json.loads(handler("submit_plan", {"title": "x", "items": [
                {"shot_id": "S99", "seconds": 2, "caption": "", "why": ""}]}).text)
            assert r["ok"] is False
        ids = [x["id"] for x in shots][::-1]
        r = json.loads(handler("submit_plan", {"title": "Sea Days", "items": [
            {"shot_id": i, "seconds": 2.5, "caption": "hi", "why": f"because {i}"} for i in ids]}).text)
        assert r["ok"] is True
        return "Opened on the strongest shot."


class BrokenLLM(VisionLLM):
    def analyze_images(self, *a, **k):
        raise RuntimeError("down")

    def run_tools(self, *a, **k):
        raise RuntimeError("down")


@requires_ffmpeg
def test_director_recovers_from_invalid_plan(clips, tmp_path):
    s = _session(clips, tmp_path)
    plan = DirectorSelector(ScriptedDirector()).select(s)
    assert plan.selector == "director" and plan.title == "Sea Days"
    assert [i.shot_id for i in plan.items] == [c.id for c in s.candidates()][::-1]
    assert s.scores[plan.items[0].shot_id].reason.startswith("because")


@requires_ffmpeg
def test_director_falls_back_to_heuristic(clips, tmp_path):
    s = _session(clips, tmp_path)
    plan = DirectorSelector(BrokenLLM()).select(s)
    assert plan.selector == "heuristic" and plan.items
    assert any("fallback" in n for n in plan.notes)


@requires_ffmpeg
def test_heuristic_fits_target_duration(clips, tmp_path):
    plan = HeuristicSelector().select(_session(clips, tmp_path, duration=8.0))
    assert abs(plan.seconds - 8.0) < 1.0
    cuts = [(i.shot_id, i.start) for i in plan.items]
    assert len(set(cuts)) == len(cuts)  # no moment used twice


@requires_ffmpeg
def test_pipeline_renders_vertical_mp4_with_report(clips, tmp_path):
    out = tmp_path / "reel.mp4"
    pipe = build_pipeline({"analyzers": ["quality", "vision"], "selector": "director"},
                          llm=ScriptedDirector(bad_first=False))
    session, plan, result = pipe.run(str(clips), Brief("travel", "travel", 6.0), str(tmp_path / "w"), str(out))
    assert (result.width, result.height) == (1080, 1920)
    assert result.seconds > 3 and result.size_mb < 16
    report = json.loads(out.with_suffix(".json").read_text())
    assert report["plan"]["selector"] == "director" and report["scores"]


def test_tool_results_are_text_plus_images():
    r = ToolResult("{}", [("Shot S1", "/x.jpg")])
    assert isinstance(r.text, str) and r.images[0][0] == "Shot S1"


# --- timeline mode (Gemini default): model returns timestamped cuts -----------------------

LADAKH = ("We went to ladhak, I have 3 videos and 3 photos. "
          "I want a fast paced cinimatic video with uplifting music")


def test_brief_reads_pace_style_and_genre_from_request():
    b = brief_from_prompt(LADAKH, 20)
    assert (b.pace, b.style, b.genre) == ("fast", "cinematic", "travel")
    assert b.shot_seconds < brief_from_prompt("calm travel video").shot_seconds


# --- playbooks are user input: JSON files, derived rules, per-run overrides --------------

TRAVEL = {  # the playbook as supplied by the user
    "genre": "travel",
    "hook": ("Open with the single most striking motion shot or landmark reveal in the first 1-3 seconds, "
             "with a centered white sans-serif text overlay in the upper third (place name or short title)."),
    "structure": ["hook shot with text overlay", "3-5 highlight moments, mixing wide and close-up",
                  "scenic travel or transition sequence", "calm closing shot"],
    "pacing": {"avg_clip_seconds": 1.5, "cuts_per_10s": 6.5,
               "pace_notes": "Fast, rhythmic, hard cuts. Allow the closing shot to run longer (2-3 seconds)."},
    "length_seconds": {"min": 15, "max": 25},
    "visual_treatment": {"grade": "Warm, natural, earthy color grade", "transitions": "Hard cuts only",
                         "text_style": "Centered white sans-serif text overlay in the upper third"},
    "selection_rules": {"prefer": ["motion", "scenery"],
                        "avoid": ["shaky clips", "any single clip longer than 3 seconds"]},
}


def test_rules_are_derived_from_the_playbook_wording():
    r = playbooks.rules(TRAVEL)
    assert r.cut_seconds == 1.5 and r.max_clip_seconds == 3.0 and r.closing_seconds == (2.0, 3.0)
    assert (r.length_min, r.length_max, r.default_duration()) == (15, 25, 20)
    assert r.hard_cuts and r.text_position == "upper_third" and r.text_on == "hook"
    assert r.text_color == "white" and "colorbalance" in r.grade_filter  # "warm"


def test_explicit_fields_override_derived_rules():
    pb = playbooks.apply_overrides(TRAVEL, ["pacing.max_clip_seconds=2.5", "pacing.closing_seconds=[3,4]",
                                            "visual_treatment.grade=cool moody",
                                            "visual_treatment.transitions=crossfades"])
    r = playbooks.rules(pb)
    assert r.max_clip_seconds == 2.5 and r.closing_seconds == (3.0, 4.0) and not r.hard_cuts
    assert "bs=0.05" in r.grade_filter and "brightness=-0.04" in r.grade_filter
    assert TRAVEL["pacing"].get("max_clip_seconds") is None  # original untouched


def test_bad_playbooks_and_overrides_are_rejected(tmp_path):
    bad = tmp_path / "x.json"
    bad.write_text('{"genre": "x", "hook": "h", "structure": []}')
    with pytest.raises(playbooks.PlaybookError):
        playbooks.load_file(str(bad))
    with pytest.raises(playbooks.PlaybookError):
        playbooks.apply_overrides(TRAVEL, ["no-equals-sign"])
    with pytest.raises(playbooks.PlaybookError):
        playbooks.get_playbook("../etc/passwd")


def test_user_playbook_dir_overrides_bundled_and_adds_genres(tmp_path, monkeypatch):
    mine = {**TRAVEL, "pacing": {**TRAVEL["pacing"], "avg_clip_seconds": 2.0}}
    (tmp_path / "travel.json").write_text(json.dumps(mine))
    (tmp_path / "wedding.json").write_text(json.dumps({**TRAVEL, "genre": "wedding"}))
    monkeypatch.setenv("REEL_PLAYBOOK_DIR", str(tmp_path))
    assert playbooks.get_playbook("travel")["pacing"]["avg_clip_seconds"] == 2.0
    assert "wedding" in playbooks.available()
    assert brief_from_prompt("our wedding highlights").genre == "wedding"


def test_brief_playbook_is_used_over_the_genre_file():
    b = brief_from_prompt("travel reel", 20)
    b.playbook = playbooks.apply_overrides(TRAVEL, ["pacing.avg_clip_seconds=2.6"])
    assert b.shot_seconds == 2.6
    assert '"avg_clip_seconds": 2.6' in playbooks.prompt_block(b)


@requires_ffmpeg
def test_heuristic_follows_playbook_clip_limit_and_closing(clips, tmp_path):
    s = _session(clips, tmp_path, duration=8.0)
    s.brief.playbook = TRAVEL
    plan = HeuristicSelector().select(s)
    assert all(i.seconds <= 3.0 for i in plan.items)
    assert 2.0 <= plan.items[-1].seconds <= 3.0 or any("closing" in n for n in plan.notes)


class FakeVideoLLM(VisionLLM):
    """Gemini-like: watches videos, answers with cuts. Records what it was sent."""
    name, supports_video = "fake-video", True

    def __init__(self, answer):
        self.answer, self.sent = answer, {}

    def analyze_videos(self, prompt, videos, images, schema):
        self.sent = {"prompt": prompt, "videos": videos, "images": images, "schema": schema}
        return self.answer(videos, images)

    def analyze_images(self, prompt, images, schema):
        raise AssertionError("video-capable model should not get frames")

    def run_tools(self, *a, **k):
        raise NotImplementedError


class FakeFramesLLM(FakeVideoLLM):
    name, supports_video = "fake-frames", False

    def analyze_images(self, prompt, images, schema):
        self.sent = {"prompt": prompt, "images": images}
        return self.answer([], images)


def _cuts(session):
    a = next(x for x in session.assets if x.kind == "video" and x.duration >= 6)
    photo = next(x for x in session.assets if x.kind == "photo")
    return {"title": "Ladakh Rush", "summary": "Fast cuts.", "items": [
        {"asset_id": a.id, "start": 1.0, "end": 2.5, "caption": "Ladakh", "reason": "strong opener"},
        {"asset_id": photo.id, "start": 0, "end": 1.5, "caption": "", "reason": "colour"},
        {"asset_id": a.id, "start": 1.2, "end": 2.4, "caption": "", "reason": "repeat, must be dropped"},
        {"asset_id": "A99", "start": 0, "end": 2, "caption": "", "reason": "unknown, must be dropped"},
        {"asset_id": a.id, "start": 4.0, "end": 99, "caption": "", "reason": "end past clip, clamp"},
    ]}


@requires_ffmpeg
def test_fit_timeline_clamps_drops_and_dedupes(clips, tmp_path):
    s = _session(clips, tmp_path, duration=8.0)
    plan, notes = fit_timeline(s, _cuts(s), "timeline")
    assert [i.reason for i in plan.items] == ["strong opener", "colour", "end past clip, clamp"]
    last = plan.items[-1]
    dur = next(a.duration for a in s.assets if a.id == last.asset_id)
    assert last.start + last.seconds <= dur + 0.01
    assert any("unknown" in n for n in notes) and any("repeated" in n for n in notes)


@requires_ffmpeg
def test_fit_timeline_trims_to_target(clips, tmp_path):
    s = _session(clips, tmp_path, duration=3.0)
    plan, notes = fit_timeline(s, _cuts(s), "timeline")
    assert plan.seconds <= 3.0 * 1.1 + 0.5 and any("trimmed" in n for n in notes)


@requires_ffmpeg
def test_timeline_sends_proxies_to_video_model(clips, tmp_path):
    s = _session(clips, tmp_path, duration=8.0)
    llm = FakeVideoLLM(lambda v, i: _cuts(s))
    plan = TimelineSelector(llm).select(s)
    assert plan.selector == "timeline" and plan.title == "Ladakh Rush"
    assert len(llm.sent["videos"]) == sum(a.kind == "video" for a in s.assets)
    assert all("/proxies/" in p for _, p in llm.sent["videos"])
    assert len(llm.sent["images"]) == sum(a.kind == "photo" for a in s.assets)
    assert "too dark" in llm.sent["prompt"]  # quality filter results are passed on


@requires_ffmpeg
def test_timeline_uses_frames_for_image_only_models(clips, tmp_path):
    s = _session(clips, tmp_path, duration=8.0)
    llm = FakeFramesLLM(lambda v, i: _cuts(s))
    plan = TimelineSelector(llm).select(s)
    assert plan.selector == "timeline" and llm.sent["images"]
    assert all(label.startswith("Shot ") for label, _ in llm.sent["images"])


@requires_ffmpeg
def test_timeline_falls_back_when_model_fails(clips, tmp_path):
    s = _session(clips, tmp_path, duration=8.0)
    plan = TimelineSelector(FakeVideoLLM(lambda v, i: {"items": []})).select(s)
    assert plan.selector == "heuristic" and any("fallback" in n for n in plan.notes)


@requires_ffmpeg
def test_pipeline_renders_timestamped_cuts(clips, tmp_path):
    out = tmp_path / "reel.mp4"

    def answer(videos, images):  # labels look like "Video A1 (a.mp4, 6.0s)"
        vid, photo = videos[0][0].split()[1], images[0][0].split()[1]
        return {"title": "Ladakh Rush", "summary": "", "items": [
            {"asset_id": vid, "start": 1.0, "end": 2.5, "caption": "Ladakh", "reason": "opener"},
            {"asset_id": photo, "start": 0, "end": 1.5, "caption": "", "reason": "photo"},
            {"asset_id": vid, "start": 4.0, "end": 5.5, "caption": "", "reason": "payoff"}]}

    pipe = build_pipeline({"selector": "timeline"}, llm=FakeVideoLLM(answer))
    _, plan, result = pipe.run(str(clips), brief_from_prompt(LADAKH, 6.0), str(tmp_path / "w"), str(out))
    assert plan.selector == "timeline" and plan.items[0].start == 1.0
    assert (result.width, result.height) == (1080, 1920)
    assert abs(result.seconds - plan.seconds) < 0.5


def test_gemini_uploads_videos_then_deletes_them(tmp_path):
    pytest.importorskip("google.genai")
    from types import SimpleNamespace as N

    from app.reel.llm import GeminiVision

    img = tmp_path / "p.jpg"
    img.write_bytes(b"\xff\xd8\xff\xd9")
    log = []

    class Files:
        def upload(self, file):
            log.append(("upload", file))
            return N(name="files/1", state=N(name="PROCESSING"))

        def get(self, name):
            return N(name=name, state=N(name="ACTIVE"))

        def delete(self, name):
            log.append(("delete", name))

    class Models:
        def generate_content(self, model, contents, config):
            log.append(("generate", model, len(contents)))
            return N(text='```json\n{"title": "t", "summary": "", "items": []}\n```')

    llm = GeminiVision(model="gemini-test", client=N(files=Files(), models=Models()))
    out = llm.analyze_videos("p", [("Video A1", str(img))], [("Photo A2", str(img))], {"type": "object"})
    assert out["title"] == "t"
    assert [x[0] for x in log] == ["upload", "generate", "delete"]
    assert log[1] == ("generate", "gemini-test", 4)  # label + file + label + image part
