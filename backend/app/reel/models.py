"""Data objects passed between pipeline stages. All are JSON-serialisable via to_dict()."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Literal

Kind = Literal["video", "photo"]
Role = Literal["hook", "build", "payoff", "skip"]


@dataclass
class Brief:
    """What the user asked for."""
    prompt: str = "cinematic travel reel"
    genre: str = "travel"
    duration: float = 30.0
    music_path: str | None = None


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
    shot_id: str
    seconds: float
    caption: str = ""


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
