"""Real FFmpeg: intake validation, quality filter, scene split, rendering, compression, e2e.
Skipped automatically where ffmpeg/ffprobe are missing (run inside the Docker image)."""
from pathlib import Path

import pytest

from app import playbooks
from app.media import ffmpeg
from app.media.intake import MediaRejected, inspect
from app.media.samples import sample_files
from app.models import GeneratedAsset, GenerationJob, VideoPreferences
from app.pipeline import contracts as C
from app.pipeline.builtin.quality import QualityFilter
from app.pipeline.builtin.render import Renderer
from app.pipeline.builtin.scenes import SceneSplitter
from tests.conftest import bodies, button, drain, outbound, requires_ffmpeg, say, wa_button, wa_media

pytestmark = requires_ffmpeg


@pytest.fixture(scope="module")
def samples():
    return sample_files()


def asset(path, i, kind="video"):
    info = inspect(path, "video/mp4" if kind == "video" else "image/jpeg", Path(path).stat().st_size)
    return C.SourceAsset(asset_id=f"a{i}", kind=info.kind, order_index=i, file_path=path, duration=info.duration,
                         width=info.width, height=info.height, has_audio=info.has_audio)


def ctx(target=12.0, genre="travel"):
    return C.EditContext(genre=genre, target_seconds=target, playbook=playbooks.get(genre).model_dump(mode="json"))


def test_intake_accepts_real_media_and_rejects_fakes(samples, tmp_path):
    info = inspect(samples[0][0], "video/mp4", Path(samples[0][0]).stat().st_size)
    assert info.kind == "video" and info.codec == "h264" and info.has_audio and 5.5 < info.duration < 6.5
    photo = inspect(samples[-1][0], "image/jpeg", Path(samples[-1][0]).stat().st_size)
    assert photo.kind == "photo" and photo.width == 1600
    fake = tmp_path / "evil.mp4"
    fake.write_bytes(b"#EXTM3U\n#EXT-X-MEDIA-SEQUENCE:0\nhttp://169.254.169.254/latest\n")
    with pytest.raises(MediaRejected):
        inspect(str(fake), "video/mp4", fake.stat().st_size)
    with pytest.raises(MediaRejected):
        inspect(samples[0][0], "application/pdf", 10)
    with pytest.raises(MediaRejected):
        inspect(samples[0][0], "video/mp4", 10 ** 10)


def test_quality_filter_drops_dark_and_duplicate(samples):
    assets = [asset(p, i, "photo" if m.startswith("image") else "video") for i, (p, m) in enumerate(samples)]
    out = QualityFilter().run(C.QualityInput(assets=assets, context=ctx()))
    by_name = {Path(samples[int(r.asset_id[1:])][0]).name: r for r in out.results}
    assert not by_name["03_too_dark.mp4"].keep and "dark" in by_name["03_too_dark.mp4"].flags
    waves = [by_name["04_waves.mp4"], by_name["05_waves_again.mp4"]]
    assert sum(not w.keep for w in waves) == 1 and any("duplicate" in w.flags for w in waves)
    assert by_name["02_city_walk.mp4"].keep


def test_scene_split_and_full_render(samples, tmp_path):
    assets = [asset(samples[0][0], 0), asset(samples[1][0], 1), asset(samples[-1][0], 2, "photo")]
    scenes = SceneSplitter().run(C.SceneSplitInput(assets=assets, context=ctx(), work_dir=str(tmp_path)))
    assert scenes.segments and all(Path(f).exists() for s in scenes.segments for f in s.frame_paths)
    items = [
        C.PlanItem(segment_id="a", asset_id="a0", kind="video", source_start=1, source_end=4, role="hook", focus_x=0.3),
        C.PlanItem(segment_id="b", asset_id="a2", kind="photo", source_start=0, source_end=3, role="build"),
        C.PlanItem(segment_id="c", asset_id="a1", kind="video", source_start=0.5, source_end=3.5, role="payoff"),
    ]
    total = 9 - 2 * 0.35  # fade transitions overlap
    (tmp_path / "captions.ass").write_text(
        "[Script Info]\nPlayResX: 1080\nPlayResY: 1920\n\n[V4+ Styles]\nFormat: Name, Fontname, Fontsize\n"
        "Style: Default,DejaVu Sans,70\n\n[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, "
        "MarginV, Effect, Text\nDialogue: 0,0:00:00.50,0:00:02.00,Default,,0,0,0,,Hello world\n")
    music = tmp_path / "m.wav"
    ffmpeg.run(["-f", "lavfi", "-i", "sine=frequency=220:sample_rate=44100", "-t", "12", str(music)])
    narr = tmp_path / "n.wav"
    ffmpeg.run(["-f", "lavfi", "-i", "sine=frequency=440:sample_rate=44100", "-t", "4", str(narr)])
    out = Renderer().run(C.RenderInput(items=items, assets=assets, context=ctx(total), narration_path=str(narr),
                                       subtitles_path=str(tmp_path / "captions.ass"), music_path=str(music),
                                       title="Sample trip", width=720, height=1280, target_bytes=15 * 1024 * 1024,
                                       work_dir=str(tmp_path)))
    rep = ffmpeg.validate_mp4(out.video_path, 720, 1280, total, tolerance=0.6)
    assert rep.ok, rep.problems


def test_compression_meets_target(samples, tmp_path):
    assets = [asset(samples[1][0], 0)]
    items = [C.PlanItem(segment_id="a", asset_id="a0", kind="video", source_start=0, source_end=6, role="hook")]
    out = Renderer().run(C.RenderInput(items=items, assets=assets, context=ctx(6), width=1080, height=1920,
                                       target_bytes=900_000, work_dir=str(tmp_path)))
    assert out.size_bytes <= 900_000 and out.width == 1080


def test_end_to_end_with_sample_footage(client, db):
    for path, mime in sample_files():
        wa_media(client, Path(path).read_bytes(), mime)
    drain()
    say(client, db, "That's all. Make it a cinematic travel reel with a calm voiceover and soft music.")
    plan = outbound(db)[-1].body
    assert "Plan for your travel reel" in plan and "Skipped:" in plan and "too dark" in plan
    wa_button(client, button(db, "confirm:"))
    drain()
    job = db.query(GenerationJob).one()
    assert job.status == "completed", (job.error_code, job.error_message, bodies(db)[-3:])
    video = db.query(GeneratedAsset).filter_by(kind="video").one()
    from app.storage import get_storage

    path = get_storage().local_path(video.storage_key, "/tmp")
    prefs = db.query(VideoPreferences).one()
    rep = ffmpeg.validate_mp4(path, 720, 1280, prefs.plan["total_seconds"], tolerance=1.5)
    assert rep.ok and rep.size_bytes <= 16 * 1024 * 1024, rep.problems
    assert video.is_demo  # mock TTS / rules agent → labelled as demo output
    assert any("Demo mode" in b for b in bodies(db))
