"""Samplers: split assets into shots and grab representative frames."""
from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path

from PIL import Image

from app.reel.media import grab_frame, scene_cuts
from app.reel.models import Asset, Shot

PHOTO_SECONDS = 3.0


class Sampler(ABC):
    name = "base"

    @abstractmethod
    def sample(self, assets: list[Asset], workdir: str) -> list[Shot]: ...


def contact_sheet(frames: list[str], out: str, height: int = 240) -> str:
    """Frames side by side, so one image shows a shot's start, middle and end."""
    imgs = []
    for f in frames:
        im = Image.open(f).convert("RGB")
        imgs.append(im.resize((max(1, int(im.width * height / im.height)), height)))
    sheet = Image.new("RGB", (sum(i.width for i in imgs) + 8 * (len(imgs) - 1), height), "black")
    x = 0
    for im in imgs:
        sheet.paste(im, (x, 0))
        x += im.width + 8
    sheet.save(out, "JPEG", quality=75)
    return out


class FFmpegSceneSampler(Sampler):
    """Scene cuts via ffmpeg, long scenes split into <= max_len pieces, 3 frames per shot."""

    name = "ffmpeg_scene"

    def __init__(self, threshold: float = 0.3, min_len: float = 1.0, max_len: float = 6.0):
        self.threshold, self.min_len, self.max_len = threshold, min_len, max_len

    def sample(self, assets: list[Asset], workdir: str) -> list[Shot]:
        fdir = Path(workdir) / "frames"
        fdir.mkdir(parents=True, exist_ok=True)
        shots: list[Shot] = []
        for a in assets:
            if a.kind == "photo":
                sid = f"S{len(shots) + 1}"
                frame = str(fdir / f"{sid}_0.jpg")
                im = Image.open(a.path).convert("RGB")
                im.thumbnail((320, 320))
                im.save(frame, "JPEG", quality=85)
                shots.append(Shot(sid, a.id, "photo", 0.0, PHOTO_SECONDS, [frame] * 3))
                continue
            bounds = [0.0] + [t for t in scene_cuts(a.path, self.threshold)
                              if 0.5 < t < a.duration - 0.5] + [a.duration]
            for s, e in zip(bounds, bounds[1:]):
                t = s
                while e - t >= self.min_len:
                    end = min(t + self.max_len, e)
                    sid = f"S{len(shots) + 1}"
                    frames = []
                    for k, frac in enumerate((0.15, 0.5, 0.85)):
                        out = str(fdir / f"{sid}_{k}.jpg")
                        if grab_frame(a.path, t + (end - t) * frac, out):
                            frames.append(out)
                    if len(frames) == 3:
                        shake = str(fdir / f"{sid}_shake.jpg")
                        mid = t + (end - t) * 0.5
                        shots.append(Shot(sid, a.id, "video", round(t, 2), round(end, 2), frames,
                                          shake_frame=shake if grab_frame(a.path, mid + 0.2, shake) else None))
                    t = end
        for s in shots:
            s.strip = contact_sheet(s.frames, str(fdir / f"{s.id}_strip.jpg"))
        return shots
