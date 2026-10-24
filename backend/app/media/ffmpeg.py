"""Sandboxed FFmpeg/ffprobe wrappers.

User media is untrusted: every call runs with a timeout, CPU/memory rlimits, and
`-protocol_whitelist file,pipe` on inputs so a crafted file (e.g. an HLS playlist
disguised as MP4) can't make FFmpeg fetch URLs or read other files.
"""
from __future__ import annotations

import json
import resource
import subprocess
import textwrap
from dataclasses import dataclass, field
from pathlib import Path

from app.config import get_settings

SAFE_IN = ["-protocol_whitelist", "file,pipe"]


class FFmpegError(Exception):
    pass


def _limits() -> None:  # runs in the child process
    resource.setrlimit(resource.RLIMIT_CPU, (900, 900))
    resource.setrlimit(resource.RLIMIT_AS, (6 * 1024**3, 6 * 1024**3))
    resource.setrlimit(resource.RLIMIT_FSIZE, (2 * 1024**3, 2 * 1024**3))


def _exec(cmd: list[str], cwd: str | None, timeout: int) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(cmd, capture_output=True, cwd=cwd, timeout=timeout, preexec_fn=_limits)
    except FileNotFoundError as e:
        raise FFmpegError(f"{cmd[0]} is not installed") from e
    except subprocess.TimeoutExpired as e:
        raise FFmpegError("ffmpeg timed out") from e


def run(args: list[str], cwd: str | None = None, timeout: int = 900) -> None:
    s = get_settings()
    cmd = [s.ffmpeg_path, "-y", "-hide_banner", "-nostdin", "-loglevel", "error", "-threads", str(s.ffmpeg_threads), *args]
    r = _exec(cmd, cwd, timeout)
    if r.returncode != 0:
        raise FFmpegError(r.stderr.decode(errors="ignore")[-800:])


def run_capture(args: list[str], timeout: int = 300) -> bytes:
    s = get_settings()
    r = _exec([s.ffmpeg_path, "-hide_banner", "-nostdin", "-loglevel", "error", *args], None, timeout)
    if r.returncode != 0:
        raise FFmpegError(r.stderr.decode(errors="ignore")[-800:])
    return r.stdout


def run_stderr(args: list[str], timeout: int = 300) -> str:
    s = get_settings()
    r = _exec([s.ffmpeg_path, "-hide_banner", "-nostdin", *args], None, timeout)
    return r.stderr.decode(errors="ignore")


def probe(path: str) -> dict:
    s = get_settings()
    r = _exec([s.ffprobe_path, *SAFE_IN, "-v", "error", "-print_format", "json", "-show_format", "-show_streams", path],
              None, 60)
    if r.returncode != 0:
        raise FFmpegError(r.stderr.decode(errors="ignore")[-400:] or "ffprobe failed")
    return json.loads(r.stdout or b"{}")


def duration(path: str) -> float:
    return float(probe(path).get("format", {}).get("duration") or 0.0)


def available() -> bool:
    s = get_settings()
    try:
        for b in (s.ffmpeg_path, s.ffprobe_path):
            subprocess.run([b, "-version"], capture_output=True, timeout=10)
        return True
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False


def gray_frames(path: str, times: list[float], size: int = 32) -> list[bytes]:
    """Tiny grayscale frames (size×size bytes each) at the given timestamps."""
    out = []
    for t in times:
        raw = run_capture([*SAFE_IN, "-ss", f"{max(0.0, t):.3f}", "-i", path, "-frames:v", "1",
                           "-vf", f"scale={size}:{size}:flags=area,format=gray", "-f", "rawvideo", "-"])
        if len(raw) >= size * size:
            out.append(raw[: size * size])
    return out


def jpeg_frame(path: str, t: float, out: str, width: int = 384) -> str:
    run([*SAFE_IN, "-ss", f"{max(0.0, t):.3f}", "-i", path, "-frames:v", "1",
         "-vf", f"scale={width}:-2", "-q:v", "5", out])
    return out


def wrap(text: str, width: int) -> str:
    return "\n".join(textwrap.wrap(text, width=width)[:3])


@dataclass
class OutputReport:
    ok: bool
    width: int = 0
    height: int = 0
    duration: float = 0.0
    size_bytes: int = 0
    has_audio: bool = False
    video_codec: str | None = None
    audio_codec: str | None = None
    problems: list[str] = field(default_factory=list)


def validate_mp4(path: str, w: int, h: int, seconds: float, tolerance: float = 1.0) -> OutputReport:
    rep = OutputReport(ok=False)
    p = Path(path)
    if not p.exists() or p.stat().st_size < 1024:
        rep.problems.append("output file missing or empty")
        return rep
    rep.size_bytes = p.stat().st_size
    try:
        info = probe(path)
    except FFmpegError as e:
        rep.problems.append(f"unreadable media: {e}")
        return rep
    vs = [s for s in info.get("streams", []) if s.get("codec_type") == "video"]
    aus = [s for s in info.get("streams", []) if s.get("codec_type") == "audio"]
    if not vs:
        rep.problems.append("no video stream")
        return rep
    rep.width, rep.height = int(vs[0].get("width") or 0), int(vs[0].get("height") or 0)
    rep.video_codec = vs[0].get("codec_name")
    rep.has_audio = bool(aus)
    rep.audio_codec = aus[0].get("codec_name") if aus else None
    rep.duration = float(info.get("format", {}).get("duration") or 0)
    if (rep.width, rep.height) != (w, h):
        rep.problems.append(f"dimensions {rep.width}x{rep.height}, expected {w}x{h}")
    if abs(rep.height * 9 - rep.width * 16) > rep.height * 0.02:
        rep.problems.append("aspect ratio is not 9:16")
    if abs(rep.duration - seconds) > tolerance:
        rep.problems.append(f"duration {rep.duration:.1f}s, expected {seconds:.1f}s")
    if rep.video_codec != "h264":
        rep.problems.append(f"video codec {rep.video_codec}, expected h264")
    if rep.audio_codec != "aac":
        rep.problems.append(f"audio codec {rep.audio_codec}, expected aac")
    rep.ok = not rep.problems
    return rep
