"""Thin FFmpeg/ffprobe wrappers used by every video provider."""
from __future__ import annotations

import json
import subprocess
import textwrap
from dataclasses import dataclass, field
from pathlib import Path

from app.config import get_settings


class FFmpegError(Exception):
    pass


def _bin(name: str) -> str:
    s = get_settings()
    return s.ffmpeg_path if name == "ffmpeg" else s.ffprobe_path


def run(args: list[str], cwd: str | None = None, timeout: int = 600) -> None:
    cmd = [_bin("ffmpeg"), "-y", "-hide_banner", "-loglevel", "error", *args]
    try:
        r = subprocess.run(cmd, capture_output=True, cwd=cwd, timeout=timeout)
    except FileNotFoundError as e:
        raise FFmpegError("ffmpeg is not installed") from e
    except subprocess.TimeoutExpired as e:
        raise FFmpegError("ffmpeg timed out") from e
    if r.returncode != 0:
        raise FFmpegError(r.stderr.decode(errors="ignore")[-800:])


def probe(path: str) -> dict:
    cmd = [_bin("ffprobe"), "-v", "error", "-print_format", "json", "-show_format", "-show_streams", path]
    try:
        r = subprocess.run(cmd, capture_output=True, timeout=60)
    except FileNotFoundError as e:
        raise FFmpegError("ffprobe is not installed") from e
    if r.returncode != 0:
        raise FFmpegError(r.stderr.decode(errors="ignore")[-400:] or "ffprobe failed")
    return json.loads(r.stdout or b"{}")


def duration(path: str) -> float:
    return float(probe(path).get("format", {}).get("duration") or 0.0)


def available() -> bool:
    try:
        subprocess.run([_bin("ffmpeg"), "-version"], capture_output=True, timeout=10)
        subprocess.run([_bin("ffprobe"), "-version"], capture_output=True, timeout=10)
        return True
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False


def wrap(text: str, width: int) -> str:
    return "\n".join(textwrap.wrap(text, width=width)[:6])


def render_text_card(text: str, color: str, w: int, h: int, seconds: float, out: str,
                     banner: str | None = None, accent: str = "white") -> str:
    """Solid-colour card with centred text, gentle zoom and fades (mock visuals)."""
    work = Path(out).parent
    tf = work / (Path(out).stem + ".txt")
    tf.write_text(wrap(text, 18 if w <= 720 else 20), encoding="utf-8")
    fs = int(w / 13)
    filters = [
        f"drawtext=textfile='{tf.name}':fontcolor={accent}:fontsize={fs}:line_spacing={fs // 3}"
        f":x=(w-text_w)/2:y=(h-text_h)/2:box=1:boxcolor=black@0.25:boxborderw={fs // 2}",
    ]
    if banner:
        bf = work / (Path(out).stem + ".banner.txt")
        bf.write_text(banner, encoding="utf-8")
        filters.append(
            f"drawtext=textfile='{bf.name}':fontcolor=yellow:fontsize={int(w / 28)}"
            f":x=(w-text_w)/2:y=h*0.06:box=1:boxcolor=black@0.6:boxborderw=12"
        )
    fade_out = max(0.0, seconds - 0.3)
    filters.append(f"fade=t=in:st=0:d=0.3,fade=t=out:st={fade_out:.2f}:d=0.3")
    vf = ",".join(filters)
    run(["-f", "lavfi", "-i", f"color=c={color}:s={w}x{h}:d={seconds:.3f}:r=30", "-vf", vf,
         "-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p", "-an", Path(out).name],
        cwd=str(work))
    return out


def fit_clip(src: str, out: str, w: int, h: int, seconds: float, overlay_text: str | None = None) -> str:
    """Scale/crop any clip or image to w×h, loop/trim to `seconds`, add fades."""
    is_image = Path(src).suffix.lower() in (".jpg", ".jpeg", ".png", ".webp")
    vf = (f"scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h},setsar=1,fps=30,"
          f"fade=t=in:st=0:d=0.3,fade=t=out:st={max(0.0, seconds - 0.3):.2f}:d=0.3")
    inp = ["-loop", "1", "-i", src] if is_image else ["-stream_loop", "-1", "-i", src]
    run([*inp, "-t", f"{seconds:.3f}", "-vf", vf, "-c:v", "libx264", "-preset", "veryfast",
         "-pix_fmt", "yuv420p", "-an", out])
    return out


def fit_audio(src: str, out: str, seconds: float) -> str:
    """Speed up (if too long) and pad (if too short) narration to exactly `seconds`."""
    d = duration(src)
    filters = []
    if d > seconds - 0.2 and d > 0:
        factor = d / max(0.5, seconds - 0.2)
        while factor > 2.0:
            filters.append("atempo=2.0")
            factor /= 2.0
        if factor > 1.0:
            filters.append(f"atempo={factor:.4f}")
    filters.append(f"apad=whole_dur={seconds:.3f}")
    run(["-i", src, "-af", ",".join(filters), "-t", f"{seconds:.3f}", "-ar", "44100", "-ac", "1", out])
    return out


def silence(out: str, seconds: float) -> str:
    run(["-f", "lavfi", "-i", "anullsrc=r=44100:cl=mono", "-t", f"{seconds:.3f}", out])
    return out


MOOD_CHORDS = {
    "upbeat": ([261.63, 329.63, 392.00], 2.0),
    "calm": ([220.00, 277.18, 329.63], 0.25),
    "dramatic": ([146.83, 174.61, 220.00], 0.5),
    "inspiring": ([196.00, 246.94, 293.66], 1.0),
}


def synth_music(mood: str, seconds: float, out: str) -> str:
    """Procedurally synthesised pad — royalty-free by construction."""
    freqs, pulse = MOOD_CHORDS.get(mood, MOOD_CHORDS["calm"])
    tone = "+".join(f"sin(2*PI*{f}*t)" for f in freqs)
    expr = f"0.08*({tone})*(0.65+0.35*sin(2*PI*{pulse}*t))"
    run(["-f", "lavfi", "-i", f"aevalsrc='{expr}':s=44100:d={seconds:.3f}",
         "-af", f"afade=t=in:d=1,afade=t=out:st={max(0.0, seconds - 1.5):.2f}:d=1.5", out])
    return out


def concat(paths: list[str], out: str, workdir: str, audio: bool = False) -> str:
    lst = Path(workdir) / ("alist.txt" if audio else "vlist.txt")
    lst.write_text("".join(f"file '{Path(p).name}'\n" for p in paths), encoding="utf-8")
    args = ["-f", "concat", "-safe", "0", "-i", lst.name]
    args += ["-c:a", "pcm_s16le"] if audio else ["-c", "copy"]
    run([*args, Path(out).name], cwd=workdir)
    return out


def srt_time(t: float) -> str:
    ms = int(round(t * 1000))
    h, ms = divmod(ms, 3600_000)
    m, ms = divmod(ms, 60_000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def assemble(video: str, narration: str | None, music: str | None, srt: str | None,
             out: str, workdir: str, seconds: float, h: int) -> str:
    """Mux visuals + narration + music, burn captions, produce a web/WhatsApp-friendly MP4."""
    args = ["-i", Path(video).name]
    audio_inputs = []
    if narration:
        args += ["-i", Path(narration).name]
        audio_inputs.append(("narr", len(audio_inputs) + 1))
    if music:
        args += ["-i", Path(music).name]
        audio_inputs.append(("music", len(audio_inputs) + 1))
    if not audio_inputs:
        args += ["-f", "lavfi", "-t", f"{seconds:.3f}", "-i", "anullsrc=r=44100:cl=stereo"]
        audio_inputs.append(("sil", 1))

    fc = []
    vlabel = "0:v"
    if srt:
        fs = max(12, int(h / 80))
        style = (f"FontName=DejaVu Sans,FontSize={fs},PrimaryColour=&H00FFFFFF,OutlineColour=&H80000000,"
                 f"BorderStyle=1,Outline=2,Shadow=0,Alignment=2,MarginV={int(h / 18)}")
        fc.append(f"[0:v]subtitles={Path(srt).name}:force_style='{style}'[v]")
        vlabel = "[v]"
    names = {name: idx for name, idx in audio_inputs}
    if "narr" in names and "music" in names:
        fc.append(f"[{names['music']}:a]volume=0.35[m];[{names['narr']}:a][m]amix=inputs=2:duration=first:"
                  f"dropout_transition=0,volume=1.6[a]")
        alabel = "[a]"
    else:
        alabel = f"{audio_inputs[0][1]}:a"
    if fc:
        args += ["-filter_complex", ";".join(fc)]
    args += ["-map", vlabel, "-map", alabel, "-t", f"{seconds:.3f}",
             "-c:v", "libx264", "-preset", "veryfast", "-crf", "23", "-pix_fmt", "yuv420p",
             "-profile:v", "high", "-c:a", "aac", "-b:a", "128k", "-ar", "44100",
             "-movflags", "+faststart", Path(out).name]
    run(args, cwd=workdir)
    return out


@dataclass
class OutputReport:
    ok: bool
    width: int = 0
    height: int = 0
    duration: float = 0.0
    size_bytes: int = 0
    has_audio: bool = False
    video_codec: str | None = None
    problems: list[str] = field(default_factory=list)


def validate_mp4(path: str, expect_w: int, expect_h: int, expect_seconds: float,
                 tolerance: float = 1.0, require_audio: bool = True) -> OutputReport:
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
    rep.duration = float(info.get("format", {}).get("duration") or 0)
    if (rep.width, rep.height) != (expect_w, expect_h):
        rep.problems.append(f"dimensions {rep.width}x{rep.height}, expected {expect_w}x{expect_h}")
    if abs(rep.height * 9 - rep.width * 16) > rep.height * 0.02:
        rep.problems.append("aspect ratio is not 9:16")
    if abs(rep.duration - expect_seconds) > tolerance:
        rep.problems.append(f"duration {rep.duration:.1f}s, expected {expect_seconds}s")
    if rep.video_codec != "h264":
        rep.problems.append(f"codec {rep.video_codec}, expected h264")
    if require_audio and not rep.has_audio:
        rep.problems.append("no audio stream")
    rep.ok = not rep.problems
    return rep
