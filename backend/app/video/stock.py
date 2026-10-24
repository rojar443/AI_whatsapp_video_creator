"""Stock-footage provider: Pexels portrait videos per scene, cropped and cut with FFmpeg.

Practical, cheap MVP approach (Pexels API is free with attribution). Replaceable by a
generative video API (Runway, Luma, Veo…) by implementing generate_video() only.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import httpx

from app.video import ffmpeg
from app.video.base import GenerationError, PlannedScene, VideoProvider

log = logging.getLogger(__name__)
STYLE_HINT = {"cinematic": "cinematic", "documentary": "", "realistic": "", "animation": "colorful",
              "minimalist": "minimal"}


class StockFootageProvider(VideoProvider):
    name = "stock"
    is_mock = False

    def __init__(self, tts, script_writer=None, api_key: str | None = None, client: httpx.Client | None = None):
        super().__init__(tts, script_writer)
        if not api_key:
            raise ValueError("PEXELS_API_KEY is required for VIDEO_PROVIDER=stock")
        self.client = client or httpx.Client(timeout=30, headers={"Authorization": api_key})

    def capabilities(self) -> dict[str, Any]:
        caps = super().capabilities()
        caps["styles"] = ["cinematic", "realistic", "documentary", "minimalist"]  # no animation
        return caps

    def _search(self, query: str, min_h: int) -> str | None:
        try:
            r = self.client.get("https://api.pexels.com/videos/search",
                                params={"query": query, "orientation": "portrait", "per_page": 8, "size": "medium"})
        except httpx.HTTPError as e:
            raise GenerationError(f"stock search failed: {e}", code="provider_timeout", retryable=True) from e
        if r.status_code == 429 or r.status_code >= 500:
            raise GenerationError(f"stock provider error {r.status_code}", code="provider_unavailable", retryable=True)
        if r.status_code >= 400:
            raise GenerationError(f"stock provider rejected request ({r.status_code})", code="provider_error")
        for video in r.json().get("videos", []):
            files = sorted(
                (f for f in video.get("video_files", []) if (f.get("height") or 0) >= min_h
                 and (f.get("height") or 0) > (f.get("width") or 0) and f.get("file_type") == "video/mp4"),
                key=lambda f: f.get("height") or 0,
            )
            if files:
                return files[0]["link"]
        return None

    def generate_video(self, scenes: list[PlannedScene], prefs: dict[str, Any], workdir: str) -> list[str]:
        w, h = self.resolution(prefs)
        hint = STYLE_HINT.get(prefs.get("visual_style") or "", "")
        clips = []
        for sc in scenes:
            url = self._search(f"{sc.visual_query} {hint}".strip(), h) or \
                self._search(str(prefs.get("topic") or "nature"), h)
            if not url:
                raise GenerationError(f"no footage found for '{sc.visual_query}'", code="no_footage")
            src = Path(workdir) / f"src_{sc.index:02d}.mp4"
            try:
                with self.client.stream("GET", url, timeout=120) as resp:
                    resp.raise_for_status()
                    with src.open("wb") as fh:
                        for chunk in resp.iter_bytes():
                            fh.write(chunk)
            except httpx.HTTPError as e:
                raise GenerationError(f"footage download failed: {e}", code="provider_timeout", retryable=True) from e
            out = str(Path(workdir) / f"clip_{sc.index:02d}.mp4")
            ffmpeg.fit_clip(str(src), out, w, h, sc.seconds)
            clips.append(out)
        return clips
