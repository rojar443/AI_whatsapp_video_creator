"""Synthetic sample footage for demo mode (no real phone needed).

Clearly labelled "SAMPLE"; includes a deliberately dark clip and a near-duplicate so the
quality filter has something to drop. Real testing needs real phone footage.
"""
from __future__ import annotations

from pathlib import Path

from app.config import get_settings
from app.media import ffmpeg

SAMPLES = [
    ("01_sunrise.mp4", "mandelbrot=s=1280x720:maxiter=60,hue=h=30:s=1.3", 6, "SAMPLE sunrise"),
    ("02_city_walk.mp4", "testsrc2=s=1280x720:r=30", 6, "SAMPLE city"),
    ("03_too_dark.mp4", "testsrc2=s=1280x720:r=30,eq=brightness=-0.85", 5, "SAMPLE dark"),
    ("04_waves.mp4", "life=s=640x360:mold=10:r=30:ratio=0.1:seed=42:death_color=#3c8dbc:life_color=#f0f8ff:mold_color=#5aa9d6,scale=1280:720",
     7, "SAMPLE waves"),
    ("05_waves_again.mp4",  # same seed → near-duplicate of 04, for the quality filter
     "life=s=640x360:mold=10:r=30:ratio=0.1:seed=42:death_color=#3c8dbc:life_color=#f0f8ff:mold_color=#5aa9d6,scale=1280:720",
     7, "SAMPLE waves"),
    ("06_market.mp4", "mandelbrot=s=1280x720:maxiter=80:start_x=-0.7436:start_y=0.1318:start_scale=0.05,hue=h=200:s=1.5",
     6, "SAMPLE market"),
]


def sample_files() -> list[tuple[str, str]]:
    root = Path(get_settings().work_dir) / "samples"
    root.mkdir(parents=True, exist_ok=True)
    out = []
    for name, src, secs, label in SAMPLES:
        path = root / name
        if not path.exists():
            lf = root / f"{name}.txt"
            lf.write_text(label)
            ffmpeg.run(["-f", "lavfi", "-i", f"{src}", "-f", "lavfi", "-i", "sine=frequency=330:sample_rate=44100",
                        "-t", str(secs), "-vf", f"drawtext=textfile='{lf.name}':font='DejaVu Sans':fontsize=48:"
                        "fontcolor=white:borderw=3:x=40:y=40", "-c:v", "libx264", "-preset", "veryfast",
                        "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", name], cwd=str(root))
        out.append((str(path), "video/mp4"))
    photo = root / "07_viewpoint.jpg"
    if not photo.exists():
        ffmpeg.run(["-f", "lavfi", "-i", "mandelbrot=s=1600x1200:maxiter=200:start_scale=1.5,hue=h=90",
                    "-frames:v", "1", str(photo)])
    out.append((str(photo), "image/jpeg"))
    return out
