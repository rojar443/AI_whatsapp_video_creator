"""Footage pipeline (app.reel): quality filter, plan validation, director agent loop, rendering.
The vision model is a scripted fake, so no API calls are made."""
import json
import subprocess

import pytest

from app.reel.analyze import QualityAnalyzer
from app.reel.ingest import FolderIngestor
from app.reel.llm import ToolResult, VisionLLM
from app.reel.models import Brief, ShotScore
from app.reel.pipeline import build_pipeline
from app.reel.sample import FFmpegSceneSampler
from app.reel.select import DirectorSelector, HeuristicSelector
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
    assert abs(plan.seconds - 8.0) < 0.5
    assert len({i.shot_id for i in plan.items}) == len(plan.items)


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
