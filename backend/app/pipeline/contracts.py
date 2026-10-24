"""Typed inputs/outputs for every pipeline step.

These models ARE the contract for external agents: a Python plugin receives and returns
them as objects; an HTTP step receives `{"step", "contract_version", "input"}` as JSON and
must answer with the output model as JSON. Fields ending in `_path` hold local files; for
HTTP steps they are converted to `<name>_url` (signed, expiring download links) on the way
out, and `<name>_url` in the response is downloaded back into `<name>_path`.
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

CONTRACT_VERSION = "1"


class SourceAsset(BaseModel):
    asset_id: str
    kind: Literal["video", "photo"]
    order_index: int
    file_path: str
    duration: float | None = None
    width: int
    height: int
    has_audio: bool = False
    caption: str | None = None


class EditContext(BaseModel):
    """What the user wants, plus the genre playbook (read-only data)."""
    genre: str
    vibe: str | None = None
    style: str | None = None
    format: str = "instagram_reel"
    duration: str = "30"
    target_seconds: float = 30
    instructions: str | None = None
    narration: str = "default"
    music: str = "licensed"
    captions: bool = True
    language: str = "english"
    title_text: str | None = None
    playbook: dict


# ---- quality_filter -------------------------------------------------------------
class QualityInput(BaseModel):
    assets: list[SourceAsset]
    context: EditContext


class AssetQuality(BaseModel):
    asset_id: str
    keep: bool
    score: float = Field(ge=0, le=1)  # 1 = technically excellent
    flags: list[Literal["dark", "shaky", "duplicate", "blurry", "too_short"]] = []
    duplicate_of: str | None = None
    reason: str | None = None


class QualityOutput(BaseModel):
    results: list[AssetQuality]


# ---- scene_split ----------------------------------------------------------------
class SceneSplitInput(BaseModel):
    assets: list[SourceAsset]
    context: EditContext
    work_dir: str


class Segment(BaseModel):
    segment_id: str
    asset_id: str
    kind: Literal["video", "photo"]
    start: float = 0.0
    end: float
    frame_paths: list[str] = []  # sampled JPEG frames for scoring


class SceneSplitOutput(BaseModel):
    segments: list[Segment]


# ---- moment_scoring -------------------------------------------------------------
class ScoringInput(BaseModel):
    segments: list[Segment]
    context: EditContext


class SegmentScore(BaseModel):
    segment_id: str
    score: float = Field(ge=0, le=10)
    description: str = ""  # what's visible — grounds the script
    tags: list[str] = []
    reason: str = ""  # why it fits / doesn't fit the brief + playbook
    focus_x: float = Field(default=0.5, ge=0, le=1)  # horizontal centre of interest for 9:16 crop


class ScoringOutput(BaseModel):
    scores: list[SegmentScore]
    scorer: str = "builtin"


# ---- sequencing -----------------------------------------------------------------
class SequencingInput(BaseModel):
    segments: list[Segment]
    scores: list[SegmentScore]
    assets: list[SourceAsset]
    context: EditContext


class PlanItem(BaseModel):
    segment_id: str
    asset_id: str
    kind: Literal["video", "photo"]
    source_start: float
    source_end: float
    role: Literal["hook", "build", "payoff"]
    reason: str = ""
    description: str = ""
    focus_x: float = 0.5


class SequencingOutput(BaseModel):
    items: list[PlanItem]
    total_seconds: float
    notes: list[str] = []


# ---- script ---------------------------------------------------------------------
class ScriptInput(BaseModel):
    items: list[PlanItem]
    total_seconds: float
    context: EditContext


class ScriptLine(BaseModel):
    item_index: int
    text: str


class ScriptOutput(BaseModel):
    title: str
    lines: list[ScriptLine]

    @property
    def full_text(self) -> str:
        return " ".join(line.text.strip() for line in self.lines if line.text.strip())


# ---- narration ------------------------------------------------------------------
class NarrationInput(BaseModel):
    text: str
    voice: str = "default"  # default | cloned:<voice_profile_id>
    vibe: str | None = None
    language: str = "english"
    work_dir: str


class WordTiming(BaseModel):
    word: str
    start: float
    end: float


class NarrationOutput(BaseModel):
    audio_path: str
    duration: float
    words: list[WordTiming] = []  # empty → captions fall back to even spacing
    provider: str = "builtin"
    is_demo: bool = False


# ---- captions -------------------------------------------------------------------
class CaptionsInput(BaseModel):
    script: ScriptOutput
    words: list[WordTiming] = []
    narration_offset: float = 0.0
    items: list[PlanItem]
    item_starts: list[float]
    total_seconds: float
    position: str = "lower_third"
    work_dir: str


class CaptionsOutput(BaseModel):
    subtitles_path: str | None
    cues: int = 0


# ---- music ----------------------------------------------------------------------
class MusicInput(BaseModel):
    moods: list[str]
    energy: str = "medium"
    seconds: float
    work_dir: str


class MusicOutput(BaseModel):
    audio_path: str | None
    title: str | None = None
    artist: str | None = None
    license: str | None = None
    source_url: str | None = None
    generated: bool = False


# ---- render ---------------------------------------------------------------------
class RenderInput(BaseModel):
    items: list[PlanItem]
    assets: list[SourceAsset]
    context: EditContext
    narration_path: str | None = None
    narration_offset: float = 0.3
    subtitles_path: str | None = None
    music_path: str | None = None
    title: str | None = None
    width: int
    height: int
    target_bytes: int
    work_dir: str


class RenderOutput(BaseModel):
    video_path: str
    duration: float
    width: int
    height: int
    size_bytes: int


STEP_IO: dict[str, tuple[type[BaseModel], type[BaseModel]]] = {
    "quality_filter": (QualityInput, QualityOutput),
    "scene_split": (SceneSplitInput, SceneSplitOutput),
    "moment_scoring": (ScoringInput, ScoringOutput),
    "sequencing": (SequencingInput, SequencingOutput),
    "script": (ScriptInput, ScriptOutput),
    "narration": (NarrationInput, NarrationOutput),
    "captions": (CaptionsInput, CaptionsOutput),
    "music": (MusicInput, MusicOutput),
    "render": (RenderInput, RenderOutput),
}
STEPS = list(STEP_IO)
