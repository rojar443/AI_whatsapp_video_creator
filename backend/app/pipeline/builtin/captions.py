"""Built-in captions from the narration script (no speech recognition needed).

With word timings from the TTS provider, cues follow the voice exactly; otherwise each
script line is spread over its moment's time window.
"""
from __future__ import annotations

import re
from pathlib import Path

from app.pipeline.contracts import CaptionsInput, CaptionsOutput

MAX_WORDS = 4
MAX_CUE_SECONDS = 1.8


def ass_time(t: float) -> str:
    t = max(0.0, t)
    h, rem = divmod(t, 3600)
    m, s = divmod(rem, 60)
    return f"{int(h)}:{int(m):02d}:{s:05.2f}"


def ass_escape(text: str) -> str:
    return text.replace("\\", "").replace("{", "(").replace("}", ")").replace("\n", " ")


def header(position: str) -> str:
    margin = {"lower_third": 380, "bottom": 140, "center": 860}.get(position, 380)
    return (
        "[Script Info]\nScriptType: v4.00+\nPlayResX: 1080\nPlayResY: 1920\nWrapStyle: 0\n\n"
        "[V4+ Styles]\nFormat: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, "
        "Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, "
        "MarginL, MarginR, MarginV, Encoding\n"
        f"Style: Default,DejaVu Sans,74,&H00FFFFFF,&H00FFFFFF,&H00000000,&H64000000,-1,0,0,0,100,100,0,0,1,5,1,2,"
        f"80,80,{margin},1\n\n[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
    )


class CaptionBuilder:
    def run(self, inp: CaptionsInput) -> CaptionsOutput:
        cues: list[tuple[float, float, str]] = []
        if inp.words:
            chunk: list = []
            for w in inp.words:
                chunk.append(w)
                long = chunk[-1].end - chunk[0].start >= MAX_CUE_SECONDS
                if len(chunk) >= MAX_WORDS or long or re.search(r"[.,!?;:]$", w.word):
                    cues.append((chunk[0].start, chunk[-1].end, " ".join(x.word for x in chunk)))
                    chunk = []
            if chunk:
                cues.append((chunk[0].start, chunk[-1].end, " ".join(x.word for x in chunk)))
            cues = [(a + inp.narration_offset, b + inp.narration_offset, t) for a, b, t in cues]
        else:
            for line in inp.script.lines:
                if line.item_index >= len(inp.items):
                    continue
                start = inp.item_starts[line.item_index]
                item = inp.items[line.item_index]
                span = max(1.0, item.source_end - item.source_start)
                words = line.text.split()
                groups = [" ".join(words[i:i + MAX_WORDS]) for i in range(0, len(words), MAX_WORDS)]
                step = span / max(1, len(groups))
                for j, g in enumerate(groups):
                    cues.append((start + j * step, start + (j + 1) * step - 0.05, g))
        cues = [(a, min(b, inp.total_seconds - 0.05), t) for a, b, t in cues if a < inp.total_seconds - 0.2]
        if not cues:
            return CaptionsOutput(subtitles_path=None, cues=0)
        lines = [header(inp.position)]
        for a, b, text in cues:
            lines.append(f"Dialogue: 0,{ass_time(a)},{ass_time(max(a + 0.3, b))},Default,,0,0,0,,{ass_escape(text)}\n")
        path = Path(inp.work_dir) / "captions.ass"
        path.write_text("".join(lines), encoding="utf-8")
        return CaptionsOutput(subtitles_path=str(path), cues=len(cues))
