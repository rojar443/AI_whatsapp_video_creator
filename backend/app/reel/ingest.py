"""Ingestors: turn a source into Asset[]. FolderIngestor now; a WhatsApp batch later."""
from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path

from PIL import Image

from app.reel.media import MediaError, probe
from app.reel.models import Asset

VIDEO_EXT = {".mp4", ".mov", ".m4v", ".3gp", ".mkv", ".webm"}
PHOTO_EXT = {".jpg", ".jpeg", ".png", ".webp"}


class Ingestor(ABC):
    name = "base"

    @abstractmethod
    def load(self, source: str) -> list[Asset]: ...


class FolderIngestor(Ingestor):
    """Every supported video/photo in a folder, in filename order. Unreadable files are skipped."""

    name = "folder"

    def __init__(self, max_files: int = 30, max_video_seconds: float = 300):
        self.max_files = max_files
        self.max_video_seconds = max_video_seconds
        self.skipped: list[tuple[str, str]] = []

    def load(self, source: str) -> list[Asset]:
        root = Path(source)
        if not root.is_dir():
            raise ValueError(f"{source} is not a folder")
        files = sorted(p for p in root.iterdir() if p.suffix.lower() in VIDEO_EXT | PHOTO_EXT)
        assets: list[Asset] = []
        for p in files[: self.max_files]:
            aid = f"A{len(assets) + 1}"
            try:
                if p.suffix.lower() in PHOTO_EXT:
                    with Image.open(p) as im:
                        w, h = im.size
                    assets.append(Asset(aid, str(p), "photo", w, h, 0.0))
                else:
                    w, h, dur = probe(str(p))
                    if dur > self.max_video_seconds:
                        self.skipped.append((p.name, f"longer than {self.max_video_seconds:.0f}s"))
                        continue
                    assets.append(Asset(aid, str(p), "video", w, h, dur))
            except (MediaError, OSError) as e:
                self.skipped.append((p.name, str(e)[:120]))
        return assets
