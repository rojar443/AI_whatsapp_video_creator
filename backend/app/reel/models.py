"""Data objects passed between pipeline stages. All are JSON-serialisable via to_dict()."""
from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from typing import Any, Literal

Kind = Literal["video", "photo"]
Role = Literal["hook", "build", "payoff", "skip"]


Pace = Literal["slow", "normal", "fast"]


@dataclass
class Brief:
    """What the user asked for."""
    prompt: str = "cinematic travel reel"
    genre: str = "travel"
    duration: float = 30.0
    music_path: str | None = None
    pace: Pace = "normal"
    style: str = "cinematic"
    playbook: dict | None = None  # user-supplied playbook for this run; None = look up by genre
    title: str = ""  # user-supplied hook text (e.g. the place name); overrides the model's title

    @property
    def shot_seconds(self) -> float:
        """Average cut length: the playbook's pacing, nudged by the requested pace."""
        from app.reel.playbooks import cut_seconds

        return cut_seconds(self)


_PACE_WORDS = {"fast": ("fast", "quick", "energetic", "upbeat", "snappy", "rapid", "dynamic"),
               "slow": ("slow", "calm", "relaxed", "gentle", "chill", "peaceful")}
_STYLES = ("cinematic", "natural", "vibrant", "minimalist", "documentary")


def brief_from_prompt(prompt: str, duration: float = 30.0, genre: str | None = None,
                      music_path: str | None = None) -> Brief:
    """Read pace, style and genre from the user's request with simple keyword rules."""
    low = prompt.lower()
    pace: Pace = next((p for p, words in _PACE_WORDS.items() if any(w in low for w in words)), "normal")
    style = next((s for s in _STYLES if s in low), "cinematic")
    if genre is None:
        from app.reel.playbooks import available

        named = next((g for g in available() if re.search(rf"\b{re.escape(g)}\b", low)), None)
        daily = any(w in low for w in ("day in", "my day", "routine"))
        genre = named or ("daily" if daily else "travel")
    return Brief(prompt, genre, duration, music_path, pace, style)


@dataclass
class Asset:
    id: str
    path: str
    kind: Kind
    width: int
    height: int
    duration: float  # 0 for photos


@dataclass
class Shot:
    id: str
    asset_id: str
    kind: Kind
    start: float
    end: float
    frames: list[str]  # sampled frame image paths (start, middle, end)
    strip: str | None = None  # contact-sheet image of the frames, for vision models
    shake_frame: str | None = None  # frame ~0.2s after the middle one, for camera-shake detection

    @property
    def length(self) -> float:
        return self.end - self.start


@dataclass
class ShotScore:
    shot_id: str
    metrics: dict[str, float] = field(default_factory=dict)  # brightness, sharpness, motion
    dropped: str | None = None  # reason, if the quality filter removed it
    description: str = ""
    hook: float | None = None  # 0-10, strength as the opening shot
    story: float | None = None  # 0-10, value to the reel
    role: Role | None = None
    caption: str = ""
    reason: str = ""


@dataclass
class PlanItem:
    """One cut. Either a sampled shot (shot_id) or an exact file range (asset_id + start)."""
    shot_id: str | None
    seconds: float
    caption: str = ""
    asset_id: str | None = None
    start: float | None = None  # seconds into the asset; None = centre of the shot
    reason: str = ""


@dataclass
class ReelPlan:
    title: str
    items: list[PlanItem]
    genre: str
    selector: str  # which selector produced it
    notes: list[str] = field(default_factory=list)

    @property
    def seconds(self) -> float:
        return sum(i.seconds for i in self.items)


@dataclass
class RenderResult:
    path: str
    width: int
    height: int
    seconds: float
    size_mb: float


def to_dict(obj: Any) -> Any:
    if isinstance(obj, list):
        return [to_dict(o) for o in obj]
    if isinstance(obj, dict):
        return {k: to_dict(v) for k, v in obj.items()}
    if hasattr(obj, "__dataclass_fields__"):
        return asdict(obj)
    return obj
