"""Built-in scene split: cut clips at scene changes, window long shots, sample frames."""
from __future__ import annotations

import re
from pathlib import Path

from app.media import ffmpeg
from app.pipeline.contracts import SceneSplitInput, SceneSplitOutput, Segment

SCENE_THRESHOLD = 0.32
MAX_SEGMENTS_PER_CLIP = 8


def scene_cuts(path: str, duration: float) -> list[float]:
    log = ffmpeg.run_stderr([*ffmpeg.SAFE_IN, "-i", path, "-an", "-vf",
                             f"scale=160:-2,select='gt(scene,{SCENE_THRESHOLD})',showinfo", "-f", "null", "-"])
    cuts = sorted({round(float(m), 2) for m in re.findall(r"pts_time:([0-9.]+)", log)})
    return [c for c in cuts if 0.6 < c < duration - 0.6]


class SceneSplitter:
    def run(self, inp: SceneSplitInput) -> SceneSplitOutput:
        segments: list[Segment] = []
        window = max(2.0, float(inp.context.playbook.get("pacing", {}).get("max_clip_seconds", 4.5)))
        for a in sorted(inp.assets, key=lambda x: x.order_index):
            frames_dir = Path(inp.work_dir) / "frames"
            frames_dir.mkdir(parents=True, exist_ok=True)
            if a.kind == "photo":
                f = str(frames_dir / f"{a.asset_id}-0.jpg")
                ffmpeg.jpeg_frame(a.file_path, 0, f)
                segments.append(Segment(segment_id=f"{a.asset_id}:0", asset_id=a.asset_id, kind="photo",
                                        start=0, end=0, frame_paths=[f]))
                continue
            d = a.duration or 0.0
            bounds = [0.0, *scene_cuts(a.file_path, d), d]
            spans: list[tuple[float, float]] = []
            for s0, s1 in zip(bounds, bounds[1:], strict=False):
                t = s0
                while s1 - t > window * 1.5:
                    spans.append((t, t + window))
                    t += window
                if s1 - t >= 0.8:
                    spans.append((t, s1))
            if len(spans) > MAX_SEGMENTS_PER_CLIP:  # keep an even spread
                step = len(spans) / MAX_SEGMENTS_PER_CLIP
                spans = [spans[int(i * step)] for i in range(MAX_SEGMENTS_PER_CLIP)]
            for i, (s0, s1) in enumerate(spans):
                f = str(frames_dir / f"{a.asset_id}-{i}.jpg")
                ffmpeg.jpeg_frame(a.file_path, (s0 + s1) / 2, f)
                segments.append(Segment(segment_id=f"{a.asset_id}:{i}", asset_id=a.asset_id, kind="video",
                                        start=round(s0, 2), end=round(s1, 2), frame_paths=[f]))
        return SceneSplitOutput(segments=segments)
