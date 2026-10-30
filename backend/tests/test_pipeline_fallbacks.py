"""Fallback branches of the built-in pipeline steps, with FFmpeg faked so every path runs without media."""
from pathlib import Path

import pytest

from app import playbooks
from app.pipeline import contracts as C
from app.pipeline.builtin import quality as Q
from app.pipeline.builtin import render as R
from app.pipeline.builtin import scenes as S
from app.pipeline.builtin.captions import CaptionBuilder
from app.pipeline.builtin.sequencing import Sequencer, _pace_factor


def ctx(**over):
    return C.EditContext(genre="travel", target_seconds=over.pop("target_seconds", 12),
                         playbook=over.pop("playbook", playbooks.get("travel").model_dump(mode="json")), **over)


def video(i, has_audio=True, duration=6.0):
    return C.SourceAsset(asset_id=f"a{i}", kind="video", order_index=i, file_path=f"/in/a{i}.mp4",
                         duration=duration, width=1920, height=1080, has_audio=has_audio)


def photo(i):
    return C.SourceAsset(asset_id=f"a{i}", kind="photo", order_index=i, file_path=f"/in/a{i}.jpg",
                         width=1600, height=1200)


def item(i, start=0.0, end=3.0, kind="video", role="build"):
    return C.PlanItem(segment_id=f"s{i}", asset_id=f"a{i}", kind=kind, source_start=start, source_end=end, role=role)


# --------------------------------------------------------------------------- render (fake ffmpeg)
class FakeFFmpeg:
    def __init__(self, monkeypatch, sizes=(1000,), duration=6.0):
        self.calls: list[list[str]] = []
        self.sizes = list(sizes)
        monkeypatch.setattr(R.ffmpeg, "run", self.run)
        monkeypatch.setattr(R.ffmpeg, "duration", lambda p: duration)

    def run(self, args, cwd=None):
        self.calls.append(list(args))
        out = Path(cwd or "/") / args[-1] if cwd else Path(args[-1])
        size = self.sizes.pop(0) if out.name == "reel.mp4" and self.sizes else 10
        out.write_bytes(b"\0" * size)

    def fc(self, n=-1) -> str:
        a = self.calls[n]
        return a[a.index("-filter_complex") + 1]


def test_silent_clip_gets_a_null_audio_track(monkeypatch, tmp_path):
    f = FakeFFmpeg(monkeypatch)
    R.render_moment(item(0), video(0, has_audio=False), 0, 720, 1280, "natural", tmp_path)
    assert "anullsrc=r=44100:cl=stereo" in f.calls[0] and "1:a" in f.calls[0]
    R.render_moment(item(0), video(0, has_audio=True), 1, 720, 1280, "natural", tmp_path)
    assert "anullsrc=r=44100:cl=stereo" not in f.calls[1] and "0:a:0" in f.calls[1]


def test_unknown_grade_falls_back_to_natural(monkeypatch, tmp_path):
    f = FakeFFmpeg(monkeypatch)
    R.render_moment(item(0), video(0), 0, 720, 1280, "sepia-ish", tmp_path)
    vf = f.calls[0][f.calls[0].index("-vf") + 1]
    assert R.GRADES["natural"] in vf


def test_photo_alternates_zoom_in_and_out(monkeypatch, tmp_path):
    f = FakeFFmpeg(monkeypatch)
    R.render_moment(item(0, kind="photo"), photo(0), 0, 720, 1280, "natural", tmp_path)
    R.render_moment(item(0, kind="photo"), photo(0), 1, 720, 1280, "natural", tmp_path)
    vfs = [c[c.index("-vf") + 1] for c in f.calls]
    assert "min(zoom+0.0011" in vfs[0] and "max(zoom-0.0011" in vfs[1]


def test_join_hard_cuts_use_concat(monkeypatch, tmp_path):
    f = FakeFFmpeg(monkeypatch)
    out, starts = R.join([("/w/m0.mp4", 2.0), ("/w/m1.mp4", 3.0), ("/w/m2.mp4", 1.5)], "cut", 0.0, tmp_path)
    assert starts == [0.0, 2.0, 5.0]
    assert f.fc() == "[0:v][0:a][1:v][1:a][2:v][2:a]concat=n=3:v=1:a=1[v][a]"
    assert out.endswith("joined.mp4")


def test_join_falls_back_to_cuts_when_clips_too_short_to_fade(monkeypatch, tmp_path):
    f = FakeFFmpeg(monkeypatch)
    _, starts = R.join([("/w/m0.mp4", 1.0), ("/w/m1.mp4", 3.0)], "fade", 0.5, tmp_path)
    assert "concat" in f.fc() and starts == [0.0, 1.0]


def test_join_fade_overlaps_starts(monkeypatch, tmp_path):
    f = FakeFFmpeg(monkeypatch)
    _, starts = R.join([("/w/m0.mp4", 3.0), ("/w/m1.mp4", 3.0), ("/w/m2.mp4", 3.0)], "fade", 0.5, tmp_path)
    assert starts == [0.0, 2.5, 5.0]
    assert "xfade" in f.fc() and "acrossfade" in f.fc()


def test_join_single_clip_is_stream_copied(monkeypatch, tmp_path):
    f = FakeFFmpeg(monkeypatch)
    _, starts = R.join([("/w/m0.mp4", 3.0)], "fade", 0.5, tmp_path)
    assert starts == [0.0] and f.calls[0][:4] == ["-i", "m0.mp4", "-c", "copy"]


def _inp(tmp_path, **over):
    base = dict(items=[item(0)], assets=[video(0)], context=ctx(), width=720, height=1280,
                target_bytes=5000, work_dir=str(tmp_path))
    base.update(over)
    return C.RenderInput(**base)


@pytest.mark.parametrize("music,narr,level,has_duck,has_sink", [
    ("none", False, "0.0", False, False),
    ("original", False, "1.0", False, False),
    ("original", True, "0.35", False, True),
    ("licensed", True, "playbook", True, False),
    ("licensed", False, "playbook", False, False),
])
def test_final_pass_audio_mix(monkeypatch, tmp_path, music, narr, level, has_duck, has_sink):
    f = FakeFFmpeg(monkeypatch)
    inp = _inp(tmp_path, context=ctx(music=music), narration_path="/w/n.wav" if narr else None,
               music_path="/w/m.mp3")
    R.final_pass(inp, str(tmp_path / "joined.mp4"), 6.0, 2000, str(tmp_path / "reel.mp4"))
    fc = f.fc()
    if level == "playbook":
        level = str(float(inp.context.playbook["audio"]["original_audio_level"]))
    assert f"[0:a]volume={level}[orig]" in fc
    assert ("sidechaincompress" in fc) is has_duck
    assert ("[sc]anullsink" in fc) is has_sink
    assert ("-stream_loop" in f.calls[0]) is (music == "licensed")
    inputs = 1 + narr + (music == "licensed")
    assert f"amix=inputs={inputs}" in fc


def test_final_pass_title_and_subtitles_chain_video_labels(monkeypatch, tmp_path):
    f = FakeFFmpeg(monkeypatch)
    inp = _inp(tmp_path, title="A very long title for our summer trip", subtitles_path=str(tmp_path / "c.ass"))
    R.final_pass(inp, str(tmp_path / "joined.mp4"), 6.0, 2000, str(tmp_path / "reel.mp4"))
    fc = f.fc()
    assert "[v0]drawtext" in fc and "[vt]subtitles=c.ass[vs]" in fc
    a = f.calls[0]
    assert a[a.index("-map") + 1] == "[vs]"
    assert (tmp_path / "title.txt").read_text().count("\n") >= 1  # wrapped


def test_renderer_reencodes_until_under_target(monkeypatch, tmp_path):
    f = FakeFFmpeg(monkeypatch, sizes=[20_000, 9_000, 4_000])
    out = R.Renderer().run(_inp(tmp_path, target_bytes=5_000))
    finals = [c for c in f.calls if c[-1] == "reel.mp4"]
    assert len(finals) == 3
    kbps = [int(c[c.index("-b:v") + 1][:-1]) for c in finals]
    assert kbps[0] > kbps[1] > kbps[2]
    assert out.size_bytes == 4_000


def test_renderer_gives_up_after_three_passes(monkeypatch, tmp_path):
    f = FakeFFmpeg(monkeypatch, sizes=[20_000, 20_000, 20_000, 1])
    out = R.Renderer().run(_inp(tmp_path, target_bytes=5_000))
    assert len([c for c in f.calls if c[-1] == "reel.mp4"]) == 3
    assert out.size_bytes == 20_000  # best effort, delivery layer handles the rest


def test_renderer_caps_bitrate_by_resolution(monkeypatch, tmp_path):
    f = FakeFFmpeg(monkeypatch, sizes=[1])
    R.Renderer().run(_inp(tmp_path, target_bytes=10 ** 10))
    assert f.calls[-1][f.calls[-1].index("-b:v") + 1] == f"{R.MAX_KBPS[720]}k"
    f = FakeFFmpeg(monkeypatch, sizes=[1])
    R.Renderer().run(_inp(tmp_path, width=1080, height=1920, target_bytes=10 ** 10))
    assert f.calls[-1][f.calls[-1].index("-b:v") + 1] == f"{R.MAX_KBPS[1080]}k"


# --------------------------------------------------------------------------- quality (fake frames)
CHECKER = bytes((0 if (x + y) % 2 else 255) for y in range(32) for x in range(32))  # bright, sharp
FLAT = bytes([128] * 1024)  # blurry
INVERT = bytes(255 - b for b in CHECKER)


def frames(monkeypatch, *, first, second=None):
    calls = {"n": 0}

    def gray(path, times, size=32):
        calls["n"] += 1
        if calls["n"] % 2 == 1 or second is None:
            return [first] * len(times) if first is not None else []
        return [second] * len(times)
    monkeypatch.setattr(Q.ffmpeg, "gray_frames", gray)


def test_unreadable_asset_is_dropped(monkeypatch):
    frames(monkeypatch, first=None)
    q, h = Q.QualityFilter().analyse(video(0))
    assert not q.keep and q.reason == "no readable frames" and h is None


def test_shaky_clip_is_dropped(monkeypatch):
    frames(monkeypatch, first=CHECKER, second=INVERT)
    q, _ = Q.QualityFilter().analyse(video(0))
    assert "shaky" in q.flags and not q.keep


def test_too_short_clip_is_dropped(monkeypatch):
    frames(monkeypatch, first=CHECKER)
    q, _ = Q.QualityFilter().analyse(video(0, duration=0.5))
    assert q.flags == ["too_short"] and not q.keep


def test_blurry_is_flagged_but_kept(monkeypatch):
    frames(monkeypatch, first=FLAT)
    q, _ = Q.QualityFilter().analyse(photo(0))
    assert q.flags == ["blurry"] and q.keep and q.score < 1


def test_when_everything_fails_the_best_three_are_kept(monkeypatch):
    frames(monkeypatch, first=CHECKER)
    assets = [video(i, duration=0.3 + i * 0.1) for i in range(5)]
    out = Q.QualityFilter().run(C.QualityInput(assets=assets, context=ctx()))
    kept = [r for r in out.results if r.keep]
    assert len(kept) == 3
    assert all(r.reason.endswith("(kept: no better footage)") for r in kept)


# --------------------------------------------------------------------------- captions
def _script(*lines):
    return C.ScriptOutput(title="t", lines=[C.ScriptLine(item_index=i, text=t) for i, t in lines])


def test_word_timed_captions_flush_trailing_chunk(tmp_path):
    words = [C.WordTiming(word=w, start=i * 0.3, end=i * 0.3 + 0.25) for i, w in enumerate(["sun", "and", "sea"])]
    out = CaptionBuilder().run(C.CaptionsInput(script=_script(), words=words, narration_offset=0.5, items=[item(0)],
                                               item_starts=[0.0], total_seconds=6, work_dir=str(tmp_path)))
    assert out.cues == 1
    assert "sun and sea" in Path(out.subtitles_path).read_text()


def test_script_lines_for_missing_items_are_skipped(tmp_path):
    out = CaptionBuilder().run(C.CaptionsInput(script=_script((0, "Hello there"), (5, "orphan line")),
                                               items=[item(0)], item_starts=[0.0], total_seconds=6,
                                               work_dir=str(tmp_path)))
    assert out.cues == 1 and "orphan" not in Path(out.subtitles_path).read_text()


def test_no_cues_means_no_subtitle_file(tmp_path):
    out = CaptionBuilder().run(C.CaptionsInput(script=_script((0, "too late")), items=[item(0)],
                                               item_starts=[5.9], total_seconds=6, work_dir=str(tmp_path)))
    assert out.subtitles_path is None and out.cues == 0


# --------------------------------------------------------------------------- scenes
def test_long_clip_segments_are_thinned_evenly(monkeypatch, tmp_path):
    monkeypatch.setattr(S, "scene_cuts", lambda p, d: [])
    monkeypatch.setattr(S.ffmpeg, "jpeg_frame", lambda *a, **k: None)
    out = S.SceneSplitter().run(C.SceneSplitInput(assets=[video(0, duration=120)], context=ctx(),
                                                  work_dir=str(tmp_path)))
    assert len(out.segments) == S.MAX_SEGMENTS_PER_CLIP
    starts = [s.start for s in out.segments]
    assert starts == sorted(starts) and starts[0] == 0 and starts[-1] > 90


# --------------------------------------------------------------------------- sequencing
def test_pace_factor_slower_and_combined():
    assert _pace_factor("slow down please", None) == pytest.approx(1.3)
    assert _pace_factor("faster", "calm") == pytest.approx(0.75 * 1.15)
    assert _pace_factor(None, "funny") == pytest.approx(0.85)


def test_sequencer_without_scored_segments():
    seg = C.Segment(segment_id="x", asset_id="a0", kind="video", start=0, end=4)
    out = Sequencer().run(C.SequencingInput(segments=[seg], scores=[], assets=[video(0)], context=ctx()))
    assert out.items == [] and out.notes == ["no usable footage"]


def test_sequencer_skips_weak_segments():
    segs = [C.Segment(segment_id=f"s{i}", asset_id=f"a{i}", kind="video", start=0, end=4) for i in range(4)]
    scores = [C.SegmentScore(segment_id="s0", score=9), C.SegmentScore(segment_id="s1", score=8),
              C.SegmentScore(segment_id="s2", score=1.5), C.SegmentScore(segment_id="s3", score=1)]
    out = Sequencer().run(C.SequencingInput(segments=segs, scores=scores, assets=[video(i) for i in range(4)],
                                            context=ctx(target_seconds=60)))
    used = {i.segment_id for i in out.items}
    assert used == {"s0", "s1"}
