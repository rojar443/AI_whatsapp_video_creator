"""Development provider: renders text cards locally with FFmpeg. Output is watermarked
as a mock so it can never be mistaken for AI-generated footage."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from app.video import ffmpeg
from app.video.base import PlannedScene, VideoProvider

PALETTES = {
    "cinematic": ["#1b1f3b", "#3a1c4a", "#0f3443", "#4a1942"],
    "realistic": ["#2d4a3e", "#4a3b2d", "#2d3a4a", "#3e2d4a"],
    "animation": ["#ff6b6b", "#4ecdc4", "#ffd93d", "#6c5ce7"],
    "documentary": ["#3d3d3d", "#5a4a3a", "#2f3e46", "#4a4a3a"],
    "minimalist": ["#f5f5f5", "#e8e8e8", "#fafafa", "#eeeeee"],
}
BANNER = "DEV MOCK PREVIEW - NOT AI-GENERATED"
NAMED = {"red": "#c0392b", "blue": "#1f5fa8", "green": "#1e7d4f", "yellow": "#d4a017", "orange": "#d35400",
         "purple": "#6c3483", "pink": "#c2185b", "black": "#151515", "teal": "#00796b", "navy": "#1a2a4f",
         "gold": "#b8860b", "maroon": "#7b1e2b"}


def brand_palette(text: str | None) -> list[str] | None:
    """'#ff6600 and navy' → ['#ff6600', '#1a2a4f']. Only safe hex values reach FFmpeg."""
    import re

    if not text:
        return None
    colors = re.findall(r"#[0-9a-fA-F]{6}\b", text)
    colors += [NAMED[w] for w in re.findall(r"[a-z]+", text.lower()) if w in NAMED]
    return colors[:4] or None


class MockVideoProvider(VideoProvider):
    name = "mock"
    is_mock = True

    def generate_video(self, scenes: list[PlannedScene], prefs: dict[str, Any], workdir: str) -> list[str]:
        w, h = self.resolution(prefs)
        style = prefs.get("visual_style") or "cinematic"
        palette = brand_palette(prefs.get("brand_colors")) or PALETTES.get(style, PALETTES["cinematic"])
        text_color = "#222222" if style == "minimalist" else "white"
        clips = []
        for sc in scenes:
            out = str(Path(workdir) / f"clip_{sc.index:02d}.mp4")
            ffmpeg.render_text_card(sc.on_screen_text, palette[sc.index % len(palette)], w, h,
                                    sc.seconds, out, banner=BANNER, accent=text_color)
            clips.append(out)
        return clips
