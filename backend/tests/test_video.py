"""Real FFmpeg rendering + output validation (skipped if ffmpeg is unavailable)."""
import json
import subprocess

from app.agents.rules import RulesAgentProvider
from app.tts import MockTTSProvider
from app.video import ffmpeg
from app.video.base import GenerationError, script_from_user_text
from app.video.mock import MockVideoProvider
from tests.conftest import requires_ffmpeg

PREFS = {"topic": "morning fitness", "vibe": "motivational", "duration_seconds": 15, "visual_style": "cinematic",
         "quality": "standard", "language": "english", "voice": "female", "captions_enabled": True,
         "music": "upbeat", "pace": "fast", "call_to_action": "Follow for daily tips", "script_mode": "ai"}


def provider():
    return MockVideoProvider(MockTTSProvider(), RulesAgentProvider())


def test_scene_plan_matches_duration():
    p = provider()
    script = p.generate_script(PREFS)
    scenes = p.generate_scenes(script, PREFS)
    assert abs(sum(s.seconds for s in scenes) - 15) < 0.01
    assert scenes[-1].narration == "Follow for daily tips"
    assert p.plan(dict(PREFS, pace="slow", duration_seconds=60))[0] < p.plan(dict(PREFS, pace="fast", duration_seconds=60))[0]


def test_user_script_is_used_verbatim():
    s = script_from_user_text("First line. Second line! Third line? Fourth.", PREFS, 2)
    assert [sc.narration for sc in s.scenes] == ["First line. Second line!", "Third line? Fourth."]


@requires_ffmpeg
def test_mock_render_produces_valid_vertical_mp4(tmp_path):
    result = provider().render(PREFS, str(tmp_path))
    rep = result.report
    assert rep.ok, rep.problems
    assert (rep.width, rep.height) == (720, 1280)
    assert abs(rep.duration - 15) <= 1.0
    assert rep.has_audio and rep.video_codec == "h264"
    srt = (tmp_path / "captions.srt").read_text()
    assert "-->" in srt


@requires_ffmpeg
def test_high_quality_no_voice_no_music_no_captions(tmp_path):
    prefs = dict(PREFS, quality="high", voice="none", music="none", captions_enabled=False, duration_seconds=15)
    result = provider().render(prefs, str(tmp_path))
    assert (result.width, result.height) == (1080, 1920)
    assert result.srt_path is None
    assert result.report.has_audio  # silent track keeps players/WhatsApp happy


@requires_ffmpeg
def test_validator_catches_bad_output(tmp_path):
    bad = tmp_path / "landscape.mp4"
    ffmpeg.run(["-f", "lavfi", "-i", "color=c=red:s=640x360:d=3", "-c:v", "libx264", "-pix_fmt", "yuv420p", str(bad)])
    rep = ffmpeg.validate_mp4(str(bad), 720, 1280, 15)
    assert not rep.ok
    joined = " ".join(rep.problems)
    assert "dimensions" in joined and "9:16" in joined and "duration" in joined and "audio" in joined
    garbage = tmp_path / "garbage.mp4"
    garbage.write_bytes(b"0" * 5000)
    assert not ffmpeg.validate_mp4(str(garbage), 720, 1280, 15).ok


@requires_ffmpeg
def test_render_raises_on_invalid_output(tmp_path, monkeypatch):
    p = provider()
    monkeypatch.setattr(p, "resolution", lambda prefs: (720, 1280))
    monkeypatch.setattr(p, "validate_output", lambda path, prefs: ffmpeg.validate_mp4(path, 999, 999, 15))
    try:
        p.render(PREFS, str(tmp_path))
        raise AssertionError("expected GenerationError")
    except GenerationError as e:
        assert e.code == "invalid_output" and not e.retryable


@requires_ffmpeg
def test_synth_music_and_fit_audio(tmp_path):
    m = ffmpeg.synth_music("dramatic", 4, str(tmp_path / "m.wav"))
    assert abs(ffmpeg.duration(m) - 4) < 0.1
    long = ffmpeg.synth_music("calm", 6, str(tmp_path / "long.wav"))
    fitted = ffmpeg.fit_audio(long, str(tmp_path / "fit.wav"), 3.0)
    assert abs(ffmpeg.duration(fitted) - 3.0) < 0.1
    info = json.loads(subprocess.run(["ffprobe", "-v", "error", "-print_format", "json", "-show_streams", fitted],
                                     capture_output=True).stdout)
    assert info["streams"][0]["codec_type"] == "audio"
