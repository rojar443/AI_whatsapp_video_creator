"""Editing-preference catalog, defaults and merging.

Every value that reaches the database or the pipeline is checked against these closed
option lists, so anything a model proposes is validated first.
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from typing import Any

GENRES = ["travel", "daily_life"]
VIBES = ["cinematic", "motivational", "funny", "emotional", "professional", "educational", "calm"]
FORMATS = ["instagram_reel", "youtube_short", "vertical"]
DURATIONS = ["original", "15", "30", "60"]
STYLES = ["cinematic", "natural", "vibrant", "minimalist", "documentary"]
QUALITIES = ["standard", "high"]
MUSIC = ["licensed", "original", "none"]  # add licensed music | keep clip audio | silent
NARRATION = ["default", "none", "cloned"]
LANGUAGES = ["english"]  # English first; others after the core flow works

ENUM_FIELDS: dict[str, list[str]] = {
    "genre": GENRES, "vibe": VIBES, "format": FORMATS, "duration": DURATIONS, "style": STYLES,
    "quality": QUALITIES, "music": MUSIC, "narration": NARRATION, "language": LANGUAGES,
}
TEXT_FIELDS = {"title_text": 120, "instructions": 1000}
BOOL_FIELDS = {"captions"}
ALL_FIELDS = [*ENUM_FIELDS, *TEXT_FIELDS, *BOOL_FIELDS]

LABELS = {
    "instagram_reel": "Instagram Reel", "youtube_short": "YouTube Short", "vertical": "Vertical short",
    "daily_life": "Daily life", "original": "Original length", "licensed": "Licensed music",
    "none": "None", "default": "AI voice",
}

FIXED_DEFAULTS: dict[str, Any] = {
    "format": "instagram_reel", "duration": "30", "quality": "standard", "captions": True,
    "music": "licensed", "narration": "default", "language": "english",
}
GENRE_DEFAULTS = {
    "travel": {"vibe": "cinematic", "style": "cinematic"},
    "daily_life": {"vibe": "calm", "style": "natural"},
}
VIBE_STYLE = {"cinematic": "cinematic", "motivational": "vibrant", "funny": "vibrant", "emotional": "cinematic",
              "professional": "minimalist", "educational": "natural", "calm": "natural"}


def label(value: Any) -> str:
    if value is None:
        return "—"
    if isinstance(value, bool):
        return "On" if value else "Off"
    return LABELS.get(str(value), str(value).replace("_", " ").capitalize())


_CTRL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def clean_text(value: Any, limit: int) -> str | None:
    if value is None:
        return None
    text = _CTRL.sub("", str(value)).strip()
    return text[:limit] or None


def coerce_field(name: str, value: Any) -> Any:
    """Return a valid value for `name`, or None. Never raises."""
    if value is None:
        return None
    if name in ENUM_FIELDS:
        v = str(value).strip().lower().replace(" ", "_").replace("-", "_")
        if name == "duration":
            v = v.removesuffix("s").removesuffix("_seconds")
        return v if v in ENUM_FIELDS[name] else None
    if name in TEXT_FIELDS:
        return clean_text(value, TEXT_FIELDS[name])
    if name in BOOL_FIELDS:
        return value if isinstance(value, bool) else None
    return None


@dataclass
class Prefs:
    values: dict[str, Any] = field(default_factory=dict)
    sources: dict[str, str] = field(default_factory=dict)  # user | default | inferred

    @classmethod
    def from_row(cls, row) -> Prefs:
        return cls(values={f: getattr(row, f) for f in ALL_FIELDS}, sources=dict(row.field_sources or {}))

    def to_row(self, row) -> None:
        for f in ALL_FIELDS:
            setattr(row, f, self.values.get(f))
        row.field_sources = dict(self.sources)

    def get(self, name: str) -> Any:
        return self.values.get(name)

    def is_user(self, name: str) -> bool:
        return self.sources.get(name) == "user" and self.values.get(name) is not None

    def copy(self) -> Prefs:
        return Prefs(dict(self.values), dict(self.sources))


def apply_user_values(prefs: Prefs, proposed: dict[str, Any]) -> list[str]:
    """Merge validated user-stated values; returns changed field names. Instructions accumulate."""
    changed = []
    for name, raw in proposed.items():
        value = coerce_field(name, raw)
        if value is None:
            continue
        if name == "instructions" and prefs.values.get("instructions"):
            if value.lower() in prefs.values["instructions"].lower():
                continue
            value = clean_text(prefs.values["instructions"] + "; " + value, TEXT_FIELDS["instructions"])
        if prefs.values.get(name) != value:
            changed.append(name)
        prefs.values[name] = value
        prefs.sources[name] = "user"
    return changed


def apply_defaults(prefs: Prefs) -> None:
    for name, value in FIXED_DEFAULTS.items():
        if prefs.values.get(name) is None:
            prefs.values[name] = value
            prefs.sources[name] = "default"
    genre = prefs.values.get("genre") or "travel"
    for name, value in GENRE_DEFAULTS.get(genre, {}).items():
        if not prefs.is_user(name):
            if name == "style" and prefs.is_user("vibe"):
                value = VIBE_STYLE.get(prefs.values["vibe"], value)
            prefs.values[name] = value
            prefs.sources[name] = "inferred"


def missing_essentials(prefs: Prefs) -> list[str]:
    """The only thing we must ask: the genre (it selects the playbook)."""
    return [] if prefs.get("genre") else ["genre"]


def credits_for(seconds: float | None, quality: str | None) -> int:
    base = max(1, math.ceil((seconds or 30) / 15))
    return base * (2 if quality == "high" else 1)


def target_seconds(prefs: Prefs, available_seconds: float, playbook_range: tuple[float, float] | None = None) -> float:
    """Requested length, capped by usable footage (photos count as a few seconds each)."""
    d = prefs.get("duration") or "30"
    if d == "original":
        want = min(available_seconds, 90.0)
    else:
        want = float(d)
    return round(max(5.0, min(want, available_seconds)), 1)


def describe_changes(before: Prefs, after: Prefs, fields: list[str]) -> str:
    out = []
    for f in fields:
        if f == "instructions":
            out.append(f"instructions: {after.get(f)}")
        else:
            out.append(f"{f.replace('_', ' ')}: {label(before.get(f))} → {label(after.get(f))}")
    return "; ".join(out)
