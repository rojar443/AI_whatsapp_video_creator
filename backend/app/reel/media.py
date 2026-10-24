"""ffmpeg/ffprobe helpers for the reel pipeline (no app.config dependency)."""
from __future__ import annotations

import json
import os
import re
import subprocess

FFMPEG = os.environ.get("FFMPEG_PATH", "ffmpeg")
FFPROBE = os.environ.get("FFPROBE_PATH", "ffprobe")


class MediaError(Exception):
    pass


def ffmpeg(*args: str, timeout: int = 600) -> None:
    r = subprocess.run([FFMPEG, "-y", "-hide_banner", "-loglevel", "error", *args],
                       capture_output=True, timeout=timeout)
    if r.returncode != 0:
        raise MediaError(r.stderr.decode(errors="ignore")[-800:])


def probe(path: str) -> tuple[int, int, float]:
    """(width, height, duration) of the first video stream."""
    r = subprocess.run([FFPROBE, "-v", "error", "-select_streams", "v:0",
                        "-show_entries", "stream=width,height:format=duration",
                        "-of", "json", path], capture_output=True, timeout=60)
    if r.returncode != 0:
        raise MediaError(f"cannot read {os.path.basename(path)}")
    j = json.loads(r.stdout or b"{}")
    if not j.get("streams"):
        raise MediaError(f"no video stream in {os.path.basename(path)}")
    st = j["streams"][0]
    return int(st["width"]), int(st["height"]), float(j.get("format", {}).get("duration") or 0)


def scene_cuts(path: str, threshold: float = 0.3) -> list[float]:
    r = subprocess.run([FFMPEG, "-hide_banner", "-i", path, "-vf",
                        f"select='gt(scene,{threshold})',showinfo", "-an", "-f", "null", "-"],
                       capture_output=True, text=True, timeout=600)
    return [float(t) for t in re.findall(r"pts_time:([\d.]+)", r.stderr)]


def grab_frame(path: str, t: float, out: str, width: int = 320) -> bool:
    try:
        ffmpeg("-ss", f"{t:.3f}", "-i", path, "-frames:v", "1", "-vf", f"scale={width}:-2", out)
    except MediaError:
        return False
    return os.path.exists(out)
