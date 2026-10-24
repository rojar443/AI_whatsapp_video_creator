"""Built-in music picker.

Licensed tracks live in MUSIC_LIBRARY_DIR with a `library.yaml` manifest recording each
track's licence and source (see music/README.md). If the library is empty, a calm ambient
bed is synthesised with FFmpeg — royalty-free by construction, and labelled as generated.
"""
from __future__ import annotations

import random
from pathlib import Path

import yaml

from app.config import get_settings
from app.media import ffmpeg
from app.pipeline.contracts import MusicInput, MusicOutput

CHORDS = {"low": ([220.0, 277.18, 329.63], 0.25), "medium": ([196.0, 246.94, 293.66], 0.5),
          "high": ([261.63, 329.63, 392.0], 2.0)}


def library() -> list[dict]:
    root = Path(get_settings().music_library_dir)
    manifest = root / "library.yaml"
    if not manifest.exists():
        return []
    rows = yaml.safe_load(manifest.read_text()) or []
    out = []
    for r in rows:
        if isinstance(r, dict) and r.get("file") and r.get("license"):
            path = (root / str(r["file"])).resolve()
            if root.resolve() in path.parents and path.exists():
                out.append({**r, "path": str(path)})
    return out


class MusicPicker:
    def run(self, inp: MusicInput) -> MusicOutput:
        tracks = library()
        if tracks:
            moods = set(inp.moods)
            ranked = sorted(tracks, key=lambda t: (-len(moods & set(t.get("moods", []))),
                                                   t.get("energy") != inp.energy))
            best = [t for t in ranked if (len(moods & set(t.get("moods", []))), t.get("energy") == inp.energy)
                    == (len(moods & set(ranked[0].get("moods", []))), ranked[0].get("energy") == inp.energy)]
            t = random.choice(best)
            return MusicOutput(audio_path=t["path"], title=t.get("title"), artist=t.get("artist"),
                               license=t.get("license"), source_url=t.get("source_url"))
        freqs, pulse = CHORDS.get(inp.energy, CHORDS["medium"])
        tone = "+".join(f"sin(2*PI*{f}*t)" for f in freqs)
        expr = f"0.07*({tone})*(0.7+0.3*sin(2*PI*{pulse}*t))"
        out = str(Path(inp.work_dir) / "music.wav")
        ffmpeg.run(["-f", "lavfi", "-i", f"aevalsrc='{expr}':s=44100:d={inp.seconds + 1:.2f}",
                    "-af", f"afade=t=in:d=1.5,afade=t=out:st={max(0.0, inp.seconds - 1.5):.2f}:d=1.5", out])
        return MusicOutput(audio_path=out, title="Ambient bed (generated)", license="generated, royalty-free",
                           generated=True)
