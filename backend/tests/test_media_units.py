"""FFmpeg wrapper and media intake validation, with subprocess/probe faked (plus a few real-ffmpeg checks)."""
import subprocess
from types import SimpleNamespace

import pytest

from app.config import get_settings
from app.media import ffmpeg, intake
from app.media.intake import MediaRejected
from tests.conftest import requires_ffmpeg


def done(rc=0, out=b"", err=b""):
    return SimpleNamespace(returncode=rc, stdout=out, stderr=err)


@pytest.fixture
def fake_exec(monkeypatch):
    calls, results = [], []

    def ex(cmd, cwd, timeout):
        calls.append((cmd, cwd, timeout))
        return results.pop(0) if results else done()

    monkeypatch.setattr(ffmpeg, "_exec", ex)
    return calls, results


# ------------------------------------------------------------------ _exec / run
def test_exec_missing_binary():
    with pytest.raises(FFmpegErrorType := ffmpeg.FFmpegError, match="not installed"):
        ffmpeg._exec(["/nonexistent/ffmpeg-xyz"], None, 5)
    assert FFmpegErrorType is ffmpeg.FFmpegError


def test_exec_timeout(monkeypatch):
    def slow(*a, **k):
        raise subprocess.TimeoutExpired("ffmpeg", 1)

    monkeypatch.setattr(subprocess, "run", slow)
    with pytest.raises(ffmpeg.FFmpegError, match="timed out"):
        ffmpeg._exec(["ffmpeg"], None, 1)


def test_exec_applies_limits(monkeypatch):
    seen = {}
    monkeypatch.setattr(subprocess, "run", lambda cmd, **kw: seen.update(kw) or done())
    ffmpeg._exec(["ffmpeg"], "/tmp", 3)
    assert seen["preexec_fn"] is ffmpeg._limits and seen["timeout"] == 3 and seen["cwd"] == "/tmp"


def test_limits_sets_rlimits(monkeypatch):
    set_ = []
    monkeypatch.setattr(ffmpeg.resource, "setrlimit", lambda r, v: set_.append((r, v)))
    ffmpeg._limits()
    assert (ffmpeg.resource.RLIMIT_CPU, (900, 900)) in set_ and len(set_) == 2


def test_run_builds_command(fake_exec, monkeypatch):
    calls, _ = fake_exec
    monkeypatch.setattr(get_settings(), "ffmpeg_threads", 2)
    ffmpeg.run(["-i", "a", "b"], cwd="/w")
    cmd = calls[0][0]
    assert cmd[1:9] == ["-y", "-hide_banner", "-nostdin", "-loglevel", "error", "-threads", "2", "-i"]
    assert calls[0][1] == "/w"


def test_run_error_includes_filter_graph(fake_exec):
    _, results = fake_exec
    results.append(done(1, err=b"Invalid argument"))
    with pytest.raises(ffmpeg.FFmpegError) as ei:
        ffmpeg.run(["-i", "a", "-filter_complex", "[0]scale=1:1", "out"])
    assert "Invalid argument" in str(ei.value) and "filters: [0]scale=1:1" in str(ei.value)


def test_run_capture(fake_exec):
    _, results = fake_exec
    results += [done(0, out=b"RAW"), done(1, err=b"bad")]
    assert ffmpeg.run_capture(["x"]) == b"RAW"
    with pytest.raises(ffmpeg.FFmpegError, match="bad"):
        ffmpeg.run_capture(["x"])


def test_run_stderr(fake_exec):
    _, results = fake_exec
    results.append(done(1, err=b"Duration: 00:00:05"))
    assert ffmpeg.run_stderr(["-i", "x"]) == "Duration: 00:00:05"


def test_probe(fake_exec):
    calls, results = fake_exec
    results += [done(0, out=b'{"format": {"duration": "3.5"}}'), done(0, out=b""), done(1, err=b"")]
    assert ffmpeg.duration("v.mp4") == 3.5
    assert ffmpeg.SAFE_IN[1] in calls[0][0]
    assert ffmpeg.probe("v.mp4") == {}
    with pytest.raises(ffmpeg.FFmpegError, match="ffprobe failed"):
        ffmpeg.probe("v.mp4")


def test_available(monkeypatch):
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: done())
    assert ffmpeg.available()

    def missing(*a, **k):
        raise FileNotFoundError

    monkeypatch.setattr(subprocess, "run", missing)
    assert not ffmpeg.available()


def test_gray_frames_and_seek(fake_exec):
    calls, results = fake_exec
    results += [done(0, out=b"\x01" * 20), done(0, out=b"\x02" * 8)]
    out = ffmpeg.gray_frames("v.mp4", [0.0, 2.5], size=4)
    assert out == [b"\x01" * 16]  # second frame too short → dropped
    assert "-ss" not in calls[0][0] and calls[1][0][calls[1][0].index("-ss") + 1] == "2.500"


def test_jpeg_frame(fake_exec):
    calls, _ = fake_exec
    assert ffmpeg.jpeg_frame("v.mp4", 1, "o.jpg", width=100) == "o.jpg"
    assert "scale=100:-2" in calls[0][0]


def test_wrap_max_three_lines():
    assert ffmpeg.wrap("one two three four five six seven", 5).count("\n") == 2


# ------------------------------------------------------------------ validate_mp4
def stream(kind, codec, **kw):
    return {"codec_type": kind, "codec_name": codec, **kw}


@pytest.fixture
def mp4(tmp_path):
    p = tmp_path / "o.mp4"
    p.write_bytes(b"\x00" * 2048)
    return str(p)


def test_validate_missing_or_tiny(tmp_path):
    assert ffmpeg.validate_mp4(str(tmp_path / "nope.mp4"), 1080, 1920, 10).problems == ["output file missing or empty"]
    small = tmp_path / "s.mp4"
    small.write_bytes(b"x")
    assert not ffmpeg.validate_mp4(str(small), 1080, 1920, 10).ok


def test_validate_unreadable(mp4, monkeypatch):
    monkeypatch.setattr(ffmpeg, "probe", lambda p: (_ for _ in ()).throw(ffmpeg.FFmpegError("moov atom")))
    rep = ffmpeg.validate_mp4(mp4, 1080, 1920, 10)
    assert rep.problems == ["unreadable media: moov atom"]


def test_validate_no_video(mp4, monkeypatch):
    monkeypatch.setattr(ffmpeg, "probe", lambda p: {"streams": [stream("audio", "aac")]})
    assert ffmpeg.validate_mp4(mp4, 1080, 1920, 10).problems == ["no video stream"]


def test_validate_ok(mp4, monkeypatch):
    monkeypatch.setattr(ffmpeg, "probe", lambda p: {"streams": [stream("video", "h264", width=1080, height=1920),
                                                                 stream("audio", "aac")],
                                                     "format": {"duration": "10.4"}})
    rep = ffmpeg.validate_mp4(mp4, 1080, 1920, 10)
    assert rep.ok and rep.has_audio and rep.size_bytes == 2048


def test_validate_all_problems(mp4, monkeypatch):
    monkeypatch.setattr(ffmpeg, "probe", lambda p: {"streams": [stream("video", "vp9", width=1920, height=1080)],
                                                     "format": {}})
    rep = ffmpeg.validate_mp4(mp4, 1080, 1920, 10)
    assert not rep.ok and len(rep.problems) == 5
    assert any("aspect ratio" in p for p in rep.problems) and any("audio codec None" in p for p in rep.problems)


@requires_ffmpeg
def test_real_ffmpeg_roundtrip(tmp_path):
    out = str(tmp_path / "t.mp4")
    ffmpeg.run(["-f", "lavfi", "-i", "color=c=blue:s=108x192:d=1", "-f", "lavfi", "-i", "anullsrc=r=44100:cl=mono",
                "-t", "1", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", out])
    assert 0.5 < ffmpeg.duration(out) < 1.5
    rep = ffmpeg.validate_mp4(out, 108, 192, 1.0, tolerance=0.5)
    assert rep.ok, rep.problems
    assert len(ffmpeg.gray_frames(out, [0.0])[0]) == 32 * 32


# ------------------------------------------------------------------ intake.inspect
MP4_HEAD = b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 16
JPG_HEAD = b"\xff\xd8\xff\xe0" + b"\x00" * 16


@pytest.fixture
def media(tmp_path):
    def make(head=MP4_HEAD):
        p = tmp_path / "m"
        p.write_bytes(head)
        return str(p)
    return make


def probe_returns(monkeypatch, info=None, exc=None):
    def fake(path):
        if exc:
            raise exc
        return info
    monkeypatch.setattr(intake.ffmpeg, "probe", fake)


def video_info(codec="h264", w=1920, h=1080, dur="8", rotation=None, audio=True):
    v = {"codec_type": "video", "codec_name": codec, "width": w, "height": h}
    if rotation is not None:
        v["side_data_list"] = [{"rotation": rotation}, {"other": 1}]
    streams = [v] + ([{"codec_type": "audio"}] if audio else [])
    return {"streams": streams, "format": {"duration": dur} if dur is not None else {}}


def test_magic_ok():
    assert intake._magic_ok(MP4_HEAD, "mov") and intake._magic_ok(JPG_HEAD, "jpg")
    assert not intake._magic_ok(JPG_HEAD, "mp4") and intake._magic_ok(b"anything", "unknown")


@pytest.mark.parametrize("mime", ["application/pdf", "image/heic", None, ""])
def test_rejects_type(media, mime):
    with pytest.raises(MediaRejected, match="unsupported file type"):
        intake.inspect(media(), mime, 10)


def test_rejects_size(media):
    with pytest.raises(MediaRejected, match="larger than"):
        intake.inspect(media(JPG_HEAD), "image/jpeg", get_settings().max_image_bytes + 1)


def test_rejects_wrong_magic(media):
    with pytest.raises(MediaRejected, match="doesn't match"):
        intake.inspect(media(JPG_HEAD), "video/mp4; codecs=avc1", 10)


def test_rejects_unreadable(media, monkeypatch):
    probe_returns(monkeypatch, exc=ffmpeg.FFmpegError("x"))
    with pytest.raises(MediaRejected, match="damaged"):
        intake.inspect(media(), "video/mp4", 10)


def test_rejects_no_video_stream(media, monkeypatch):
    probe_returns(monkeypatch, {"streams": [{"codec_type": "audio"}]})
    with pytest.raises(MediaRejected, match="no picture"):
        intake.inspect(media(), "video/mp4", 10)


@pytest.mark.parametrize("w,h", [(0, 100), (100, 0), (100000, 100000)])
def test_rejects_dimensions(media, monkeypatch, w, h):
    probe_returns(monkeypatch, video_info(w=w, h=h))
    with pytest.raises(MediaRejected, match="dimensions"):
        intake.inspect(media(), "video/mp4", 10)


def test_rejects_codec(media, monkeypatch):
    probe_returns(monkeypatch, video_info(codec="prores"))
    with pytest.raises(MediaRejected, match=r"unsupported codec \(prores\)"):
        intake.inspect(media(), "video/mp4", 10)
    probe_returns(monkeypatch, video_info(codec="h264"))
    with pytest.raises(MediaRejected, match="unsupported codec"):
        intake.inspect(media(JPG_HEAD), "image/jpeg", 10)


@pytest.mark.parametrize("dur,msg", [("0.2", "too short"), (None, "too short"), ("9999", "longer than")])
def test_rejects_duration(media, monkeypatch, dur, msg):
    probe_returns(monkeypatch, video_info(dur=dur))
    with pytest.raises(MediaRejected, match=msg):
        intake.inspect(media(), "video/mp4", 10)


def test_video_ok_with_rotation(media, monkeypatch):
    probe_returns(monkeypatch, video_info(rotation=-90))
    info = intake.inspect(media(), "video/quicktime", 10)
    assert (info.kind, info.ext, info.width, info.height, info.duration, info.has_audio) == (
        "video", "mov", 1080, 1920, 8.0, True)
    probe_returns(monkeypatch, video_info(rotation=180, audio=False))
    info = intake.inspect(media(), "video/mp4", 10)
    assert (info.width, info.height, info.has_audio) == (1920, 1080, False)


def test_duration_from_stream(media, monkeypatch):
    info = video_info(dur=None)
    info["streams"][0]["duration"] = "4.0"
    probe_returns(monkeypatch, info)
    assert intake.inspect(media(), "video/mp4", 10).duration == 4.0


def test_photo_ok(media, monkeypatch):
    probe_returns(monkeypatch, video_info(codec="mjpeg", dur=None))
    info = intake.inspect(media(JPG_HEAD), "image/jpeg", 10)
    assert info.kind == "photo" and info.duration is None and info.ext == "jpg"
