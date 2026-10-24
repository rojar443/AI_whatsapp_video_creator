"""Video requirement catalog, defaults, merging, validation and the confirmation summary.

All values that reach the database or the video pipeline pass through this module, so
anything an LLM proposes is checked against a closed set of options first.
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from typing import Any

VIBES = ["motivational", "funny", "emotional", "inspirational", "educational", "calm", "energetic"]
FORMATS = ["instagram_reel", "youtube_short", "vertical"]
DURATIONS = [15, 30, 45, 60]
STYLES = ["cinematic", "realistic", "animation", "documentary", "minimalist"]
QUALITIES = ["standard", "high"]
LANGUAGES = ["english", "hindi", "malayalam", "tamil", "spanish", "french"]
VOICES = ["female", "male", "none"]
MUSIC = ["none", "upbeat", "calm", "dramatic", "inspiring"]
PACES = ["slow", "normal", "fast"]
SCRIPT_MODES = ["ai", "user"]
ASPECT_RATIOS = ["9:16"]

ENUM_FIELDS: dict[str, list] = {
    "vibe": VIBES,
    "format": FORMATS,
    "visual_style": STYLES,
    "quality": QUALITIES,
    "language": LANGUAGES,
    "voice": VOICES,
    "caption_language": LANGUAGES,
    "music": MUSIC,
    "pace": PACES,
    "script_mode": SCRIPT_MODES,
    "aspect_ratio": ASPECT_RATIOS,
}
TEXT_FIELDS = {"topic": 300, "brand_colors": 100, "call_to_action": 200, "user_script": 3000}
ALL_FIELDS = [
    "topic", "vibe", "format", "aspect_ratio", "duration_seconds", "visual_style", "quality",
    "language", "voice", "captions_enabled", "caption_language", "music", "pace",
    "brand_colors", "call_to_action", "script_mode", "user_script",
]

LABELS = {
    "instagram_reel": "Instagram Reel",
    "youtube_short": "YouTube Short",
    "vertical": "Vertical video",
    "none": "None",
}

# Questions are grouped so the user answers at most three short questions. A group is
# skipped once its key fields are known; the rest (style, quality, …) is inferred or defaulted.
QUESTION_GROUPS: list[tuple[str, list[str]]] = [
    ("topic", ["topic"]),
    ("look", ["vibe"]),
    ("format", ["format", "duration_seconds"]),
]

FIXED_DEFAULTS: dict[str, Any] = {
    "format": "instagram_reel",
    "aspect_ratio": "9:16",
    "duration_seconds": 30,
    "quality": "standard",
    "language": "english",
    "voice": "female",
    "captions_enabled": True,
    "pace": "normal",
    "script_mode": "ai",
}
VIBE_STYLE = {
    "motivational": "cinematic", "funny": "animation", "emotional": "cinematic",
    "inspirational": "cinematic", "educational": "minimalist", "calm": "minimalist",
    "energetic": "realistic",
}
VIBE_MUSIC = {
    "motivational": "upbeat", "funny": "upbeat", "emotional": "calm", "inspirational": "inspiring",
    "educational": "calm", "calm": "calm", "energetic": "upbeat",
}


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
    text = re.sub(r"\s+", " ", text) if limit <= 300 else text
    return text[:limit] or None


def coerce_field(name: str, value: Any) -> Any:
    """Return a valid value for `name` or None. Never raises on bad input."""
    if value is None:
        return None
    if name in ENUM_FIELDS:
        v = str(value).strip().lower().replace(" ", "_").replace("-", "_")
        if name == "aspect_ratio":
            v = str(value).strip()
        return v if v in ENUM_FIELDS[name] else None
    if name in TEXT_FIELDS:
        return clean_text(value, TEXT_FIELDS[name])
    if name == "duration_seconds":
        try:
            n = int(float(value))
        except (TypeError, ValueError):
            return None
        return n if n in DURATIONS else None
    if name == "captions_enabled":
        return value if isinstance(value, bool) else None
    return None


def nearest_duration(n: int) -> int:
    return min(DURATIONS, key=lambda d: abs(d - n))


def credits_for(duration: int | None, quality: str | None) -> int:
    base = max(1, math.ceil((duration or 30) / 15))
    return base * (2 if quality == "high" else 1)


@dataclass
class Prefs:
    """In-memory view of the current revision's preferences plus where each value came from."""

    values: dict[str, Any] = field(default_factory=dict)
    sources: dict[str, str] = field(default_factory=dict)  # user | default | inferred

    @classmethod
    def from_row(cls, row) -> Prefs:
        return cls(
            values={f: getattr(row, f) for f in ALL_FIELDS},
            sources=dict(row.field_sources or {}),
        )

    def to_row(self, row) -> None:
        for f in ALL_FIELDS:
            v = self.values.get(f)
            if f == "aspect_ratio" and v is None:
                v = "9:16"
            setattr(row, f, v)
        row.field_sources = dict(self.sources)

    def get(self, name: str) -> Any:
        return self.values.get(name)

    def is_user(self, name: str) -> bool:
        return self.sources.get(name) == "user" and self.values.get(name) is not None

    def copy(self) -> Prefs:
        return Prefs(values=dict(self.values), sources=dict(self.sources))


def apply_user_values(prefs: Prefs, proposed: dict[str, Any]) -> list[str]:
    """Merge validated user-stated values. Returns the list of fields that changed."""
    changed = []
    for name, raw in proposed.items():
        if name not in ALL_FIELDS:
            continue
        value = coerce_field(name, raw)
        if value is None:
            continue
        if prefs.values.get(name) != value or prefs.sources.get(name) != "user":
            if prefs.values.get(name) != value:
                changed.append(name)
            prefs.values[name] = value
            prefs.sources[name] = "user"
    if "user_script" in changed and prefs.values.get("user_script"):
        prefs.values["script_mode"] = "user"
        prefs.sources["script_mode"] = "user"
    return changed


def apply_defaults(prefs: Prefs) -> None:
    """Fill gaps with defaults. User values are never overwritten; inferred ones follow the vibe."""
    for name, value in FIXED_DEFAULTS.items():
        if prefs.values.get(name) is None:
            prefs.values[name] = value
            prefs.sources[name] = "default"
    vibe = prefs.values.get("vibe")
    if vibe:
        for name, table in (("visual_style", VIBE_STYLE), ("music", VIBE_MUSIC)):
            if not prefs.is_user(name):
                prefs.values[name] = table[vibe]
                prefs.sources[name] = "inferred"
    if not prefs.is_user("caption_language"):
        prefs.values["caption_language"] = prefs.values.get("language") or "english"
        prefs.sources["caption_language"] = "inferred"


def next_question_group(prefs: Prefs, asked: list[str]) -> str | None:
    """Return the next group to ask about, or None when we're ready to summarize."""
    for group, fields in QUESTION_GROUPS:
        if group == "topic":
            if not prefs.get("topic"):
                return "topic"
            continue
        if group in asked:
            continue
        if any(not prefs.is_user(f) for f in fields):
            return group
    return None


@dataclass
class ValidationResult:
    ok: bool
    errors: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)  # adjustments the user should know about


def validate(prefs: Prefs, capabilities: dict[str, Any]) -> ValidationResult:
    """Check prefs against the active video provider. Adjusts unsupported values in place."""
    res = ValidationResult(ok=True)
    if not prefs.get("topic"):
        res.ok = False
        res.errors.append("topic is required")
    for name, cap_key in (("quality", "qualities"), ("language", "languages"),
                          ("visual_style", "styles"), ("music", "music")):
        allowed = capabilities.get(cap_key)
        value = prefs.get(name)
        if allowed and value and value not in allowed:
            fallback = allowed[0]
            res.notes.append(
                f"{label(value)} {name.replace('_', ' ')} isn't supported by the current video "
                f"engine, so I'll use {label(fallback)}."
            )
            prefs.values[name] = fallback
            prefs.sources[name] = "default"
    max_d = capabilities.get("max_duration", 60)
    if (prefs.get("duration_seconds") or 0) > max_d:
        res.notes.append(f"The current engine supports up to {max_d} s, so I'll use {max_d} s.")
        prefs.values["duration_seconds"] = max_d
    if prefs.get("voice") != "none" and not capabilities.get("voice", True):
        res.notes.append("Voiceover isn't available right now, so the video will have no narration.")
        prefs.values["voice"] = "none"
    return res


def summarize(prefs: Prefs, credits_cost: int, credits_balance: int, notes: list[str] | None = None) -> str:
    def mark(name: str) -> str:
        return " _(suggested)_" if prefs.sources.get(name) in ("default", "inferred") else ""

    v = prefs.values
    voice = "No voiceover" if v.get("voice") == "none" else f"{label(v.get('voice'))}, {label(v.get('language'))}"
    captions = (
        f"On ({label(v.get('caption_language'))})" if v.get("captions_enabled") else "Off"
    )
    lines = [
        "Here's your video plan 🎬",
        f"• Topic: {v.get('topic')}",
        f"• Vibe: {label(v.get('vibe'))}{mark('vibe')}",
        f"• Format: {label(v.get('format'))} ({v.get('aspect_ratio') or '9:16'}){mark('format')}",
        f"• Length: {v.get('duration_seconds')} s{mark('duration_seconds')}",
        f"• Style: {label(v.get('visual_style'))}{mark('visual_style')}",
        f"• Quality: {label(v.get('quality'))}{mark('quality')}",
        f"• Voice: {voice}{mark('voice')}",
        f"• Captions: {captions}{mark('captions_enabled')}",
        f"• Music: {label(v.get('music'))}{mark('music')}",
        f"• Pace: {label(v.get('pace'))}{mark('pace')}",
    ]
    if v.get("call_to_action"):
        lines.append(f"• Call to action: {v['call_to_action']}")
    if v.get("script_mode") == "user":
        lines.append("• Script: your own")
    if notes:
        lines.append("")
        lines.extend(f"ℹ️ {n}" for n in notes)
    lines.append("")
    lines.append(f"Cost: {credits_cost} credit{'s' if credits_cost != 1 else ''} (you have {credits_balance}).")
    lines.append("Reply *yes* to generate, or tell me what to change.")
    return "\n".join(lines)


def describe_changes(before: Prefs, after: Prefs, fields: list[str]) -> str:
    parts = []
    for f in fields:
        name = f.replace("_seconds", "").replace("_", " ")
        parts.append(f"{name}: {label(before.get(f))} → {label(after.get(f))}")
    return "; ".join(parts)
