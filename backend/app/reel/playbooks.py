"""Genre playbooks: user input, loaded dynamically from JSON.

Where a run's playbook comes from (first match wins):
  1. a file the user passes for this run (--playbook FILE), with optional --set key=value overrides
  2. $REEL_PLAYBOOK_DIR/<genre>.json   (the user's playbook folder; CLI default ../../files/playbooks)
  3. backend/playbooks/<genre>.json    (bundled examples, used only as a fallback)
Files are read on every call, so edits apply to the next reel without a restart.

A playbook is data the model reads, never instructions it obeys. The whole JSON goes into the
model prompt, and the rules code must enforce (cut length, longest clip, closing shot, length,
hard cuts, text placement, colour grade) are derived from the playbook's own wording by
`rules()`. Optional explicit fields override what is derived:
  pacing.max_clip_seconds, pacing.closing_seconds [min, max], visual_treatment.ffmpeg_grade
"""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from pathlib import Path

BUNDLED_DIR = Path(__file__).resolve().parents[2] / "playbooks"
REQUIRED = ("genre", "hook", "structure", "pacing")


class PlaybookError(ValueError):
    pass


def _dirs() -> list[Path]:
    user = os.environ.get("REEL_PLAYBOOK_DIR")
    return ([Path(user)] if user else []) + [BUNDLED_DIR]


def set_user_dir(path: str | None) -> None:
    """Where the user keeps their playbooks (searched before the bundled ones)."""
    if path:
        os.environ["REEL_PLAYBOOK_DIR"] = path


def available() -> list[str]:
    names: set[str] = set()
    for d in _dirs():
        if d.is_dir():
            names.update(p.stem for p in d.glob("*.json"))
    return sorted(names)


def validate(pb: dict, source: str = "playbook") -> dict:
    missing = [k for k in REQUIRED if k not in pb]
    if missing:
        raise PlaybookError(f"{source}: missing {', '.join(missing)}")
    if not isinstance(pb["structure"], list) or not pb["structure"]:
        raise PlaybookError(f"{source}: structure must be a non-empty list")
    avg = pb["pacing"].get("avg_clip_seconds") if isinstance(pb["pacing"], dict) else None
    cuts = pb["pacing"].get("cuts_per_10s") if isinstance(pb["pacing"], dict) else None
    if not (isinstance(avg, (int, float)) and avg > 0) and not (isinstance(cuts, (int, float)) and cuts > 0):
        raise PlaybookError(f"{source}: pacing needs avg_clip_seconds or cuts_per_10s")
    return pb


def get_playbook(genre: str) -> dict:
    if not re.fullmatch(r"[a-z0-9_-]+", genre or ""):
        raise PlaybookError(f"invalid genre name {genre!r}")
    for d in _dirs():
        path = d / f"{genre}.json"
        if path.is_file():
            try:
                pb = json.loads(path.read_text(encoding="utf-8"))
            except json.JSONDecodeError as e:
                raise PlaybookError(f"{path}: invalid JSON ({e})") from e
            return validate(pb, str(path))
    raise PlaybookError(f"unknown genre '{genre}' (known: {', '.join(available()) or 'none'})")


# --- rules derived from the playbook's wording ----------------------------------------------

_NUM = r"(\d+(?:\.\d+)?)"

GRADE_WORDS = [  # keyword in visual_treatment.grade -> ffmpeg filter, applied in this order
    ("black and white", "hue=s=0"),
    ("monochrome", "hue=s=0"),
    ("warm", "colorbalance=rs=0.05:gs=0.02:bs=-0.05:rm=0.04:gm=0.01:bm=-0.04"),
    ("cool", "colorbalance=rs=-0.04:bs=0.05:rm=-0.03:bm=0.04"),
    ("earthy", "eq=saturation=0.95:gamma=0.98"),
    ("vibrant", "eq=saturation=1.22"),
    ("moody", "eq=contrast=1.12:brightness=-0.04:saturation=0.9"),
    ("bright", "eq=brightness=0.03"),
    ("cinematic", "eq=contrast=1.08:saturation=1.1:gamma=0.97"),
    ("natural", "eq=contrast=1.04:saturation=1.05"),
]
COLOURS = ("white", "yellow", "black", "red", "orange", "pink", "blue", "green")


@dataclass(frozen=True)
class Rules:
    cut_seconds: float
    max_clip_seconds: float
    closing_seconds: tuple[float, float] | None
    length_min: float | None
    length_max: float | None
    hard_cuts: bool
    grade_filter: str
    text_position: str  # upper_third | center | lower_third
    text_on: str  # hook | all | none
    text_color: str

    def default_duration(self) -> float:
        if self.length_min and self.length_max:
            return round((self.length_min + self.length_max) / 2)
        return self.length_max or self.length_min or 30.0


def _text(pb: dict, *path: str) -> str:
    cur: object = pb
    for p in path:
        cur = cur.get(p, "") if isinstance(cur, dict) else ""
    if isinstance(cur, list):
        return " ".join(str(x) for x in cur)
    return str(cur or "")


def rules(pb: dict) -> Rules:
    pacing = pb.get("pacing", {})
    avg = pacing.get("avg_clip_seconds") or round(10 / pacing["cuts_per_10s"], 2)
    prose = " ".join([_text(pb, "selection_rules", "avoid"), _text(pb, "pacing", "pace_notes"),
                      _text(pb, "hook")]).lower()

    max_clip = pacing.get("max_clip_seconds")
    if max_clip is None:
        m = re.search(rf"longer than {_NUM}\s*s", prose)
        max_clip = float(m.group(1)) if m else round(avg * 2, 2)

    closing = pacing.get("closing_seconds")
    if closing is None:
        m = re.search(rf"clos\w*[^.]*?{_NUM}\s*(?:-|to|–)\s*{_NUM}\s*s", prose)
        closing = (float(m.group(1)), float(m.group(2))) if m else None
    else:
        closing = (float(closing[0]), float(closing[1]))

    length = pb.get("length_seconds") or {}
    vt = pb.get("visual_treatment") or {}
    transitions = _text(pb, "visual_treatment", "transitions").lower()
    style = _text(pb, "visual_treatment", "text_style").lower() + " " + _text(pb, "hook").lower()

    grade = vt.get("ffmpeg_grade")
    if not grade:
        words = _text(pb, "visual_treatment", "grade").lower()
        grade = ",".join(f for w, f in GRADE_WORDS if w in words) or "eq=contrast=1.04:saturation=1.05"

    if "upper third" in style or "top" in style:
        position = "upper_third"
    elif "lower third" in style or "bottom" in style:
        position = "lower_third"
    elif "center" in style or "centre" in style or "middle" in style:
        position = "center"
    else:
        position = "lower_third"
    hook_text = "overlay" in _text(pb, "hook").lower() or "text" in _text(pb, "structure").lower()[:60]
    text_on = "hook" if hook_text and "caption" not in style else "all"
    if "no text" in style:
        text_on = "none"
    colour = next((c for c in COLOURS if re.search(rf"\b{c}\b", style)), "white")

    return Rules(
        cut_seconds=float(avg), max_clip_seconds=float(max_clip), closing_seconds=closing,
        length_min=length.get("min"), length_max=length.get("max"),
        hard_cuts="hard cut" in transitions or "no transition" in transitions,
        grade_filter=grade, text_position=position, text_on=text_on, text_color=colour,
    )


# --- user input: a playbook file per run, and single-value overrides --------------------------

def load_file(path: str) -> dict:
    p = Path(path)
    try:
        return validate(json.loads(p.read_text(encoding="utf-8")), str(p))
    except FileNotFoundError as e:
        raise PlaybookError(f"playbook file not found: {path}") from e
    except json.JSONDecodeError as e:
        raise PlaybookError(f"{path}: invalid JSON ({e})") from e


def apply_overrides(pb: dict, overrides: list[str]) -> dict:
    """['pacing.avg_clip_seconds=1.2', 'visual_treatment.grade=cool'] -> a modified copy.
    Values are parsed as JSON when possible (numbers, lists), else kept as text."""
    out = json.loads(json.dumps(pb))
    for item in overrides:
        key, sep, raw = item.partition("=")
        if not sep or not key.strip():
            raise PlaybookError(f"override must look like key.path=value, got {item!r}")
        try:
            value = json.loads(raw)
        except json.JSONDecodeError:
            value = raw
        cur = out
        parts = key.strip().split(".")
        for part in parts[:-1]:
            nxt = cur.setdefault(part, {})
            if not isinstance(nxt, dict):
                raise PlaybookError(f"cannot set {key}: {part} is not an object")
            cur = nxt
        cur[parts[-1]] = value
    return validate(out, "playbook with overrides")


# --- helpers that read the playbook attached to a request -------------------------------------

PACE_FACTOR = {"slow": 1.8, "normal": 1.0, "fast": 0.85}


def playbook_of(brief) -> dict:
    """The user-supplied playbook on the brief, else the file for its genre."""
    return brief.playbook if getattr(brief, "playbook", None) else get_playbook(brief.genre)


def rules_for(brief) -> Rules:
    return rules(playbook_of(brief))


def cut_seconds(brief) -> float:
    """Average cut length: the playbook's pacing, nudged by the user's requested pace."""
    r = rules_for(brief)
    return round(min(r.cut_seconds * PACE_FACTOR.get(brief.pace, 1.0), r.max_clip_seconds), 2)


def prompt_block(brief) -> str:
    """The full playbook (any fields it has) plus the derived limits, for model prompts."""
    pb = playbook_of(brief)
    r = rules(pb)
    limits = [f"average cut {cut_seconds(brief)}s", f"no clip longer than {r.max_clip_seconds}s"]
    if r.closing_seconds:
        limits.append(f"closing shot {r.closing_seconds[0]}-{r.closing_seconds[1]}s")
    if r.text_on == "hook":
        limits.append("text only on the opening cut (put it in caption of item 1; leave other captions empty)")
    return f"Genre playbook (JSON):\n{json.dumps(pb, indent=1)}\nEnforced limits: {'; '.join(limits)}."
