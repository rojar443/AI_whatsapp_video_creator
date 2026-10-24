"""Genre playbooks: curated, versioned YAML files → validated, read-only objects.

Files in PLAYBOOK_DIR are the source of truth (reviewed via git). On startup they're synced
into `genre_playbooks` so each project records which version it used. The agent receives
playbook content as quoted data; nothing at runtime can modify a playbook.
"""
from __future__ import annotations

import hashlib
import json
import logging
from functools import lru_cache
from pathlib import Path
from types import MappingProxyType
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import GenrePlaybook

log = logging.getLogger(__name__)


class _Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class Hook(_Frozen):
    description: str
    max_seconds: float = 3
    prefer_tags: tuple[str, ...] = ()


class Beat(_Frozen):
    role: Literal["hook", "build", "payoff"]
    share: float = Field(gt=0, le=1)
    note: str = ""


class Structure(_Frozen):
    beats: tuple[Beat, ...]
    pace_change: str = ""
    order: Literal["chronological_after_hook", "score"] = "chronological_after_hook"


class Pacing(_Frozen):
    avg_clip_seconds: float = Field(gt=0.5, le=10)
    min_clip_seconds: float = Field(gt=0.3, le=10)
    max_clip_seconds: float = Field(gt=0.5, le=15)
    cuts_per_10s: float = 3


class VisualTreatment(_Frozen):
    grade: Literal["cinematic", "natural", "vibrant", "minimalist", "documentary"] = "natural"
    transition: Literal["cut", "fade"] = "cut"
    transition_seconds: float = Field(default=0.0, ge=0, le=1)
    title_style: str = "centered_upper"
    caption_position: Literal["lower_third", "bottom", "center"] = "lower_third"


class Audio(_Frozen):
    music_energy: Literal["low", "medium", "high"] = "medium"
    music_moods: tuple[str, ...] = ()
    narration: Literal["none", "optional", "recommended"] = "optional"
    original_audio_level: float = Field(default=0.2, ge=0, le=1)
    sound_effects: str = "none"


class SelectionRules(_Frozen):
    prefer: tuple[str, ...] = ()
    avoid: tuple[str, ...] = ()


class Playbook(_Frozen):
    genre: str
    version: str
    goal: str
    hook: Hook
    structure: Structure
    pacing: Pacing
    length: dict[str, tuple[float, float]]
    visual_treatment: VisualTreatment
    audio: Audio
    selection_rules: SelectionRules

    def as_prompt_data(self) -> str:
        return json.dumps(self.model_dump(mode="json"), ensure_ascii=False)


def _checksum(data: dict) -> str:
    return hashlib.sha256(json.dumps(data, sort_keys=True).encode()).hexdigest()


@lru_cache
def load_all() -> MappingProxyType:
    """genre → Playbook (latest version per genre, by file name order)."""
    out: dict[str, Playbook] = {}
    for path in sorted(Path(get_settings().playbook_dir).glob("*.y*ml")):
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        pb = Playbook.model_validate(data)
        total = sum(b.share for b in pb.structure.beats)
        if abs(total - 1.0) > 0.05:
            raise ValueError(f"{path.name}: beat shares sum to {total}, expected 1.0")
        out[pb.genre] = pb
    if not out:
        raise RuntimeError(f"no playbooks found in {get_settings().playbook_dir}")
    return MappingProxyType(out)


def get(genre: str | None) -> Playbook:
    books = load_all()
    return books.get(genre or "") or books.get("travel") or next(iter(books.values()))


def sync_to_db(db: Session) -> None:
    for pb in load_all().values():
        data = pb.model_dump(mode="json")
        exists = db.scalar(select(GenrePlaybook).where(GenrePlaybook.genre == pb.genre,
                                                       GenrePlaybook.version == pb.version))
        if exists is None:
            db.query(GenrePlaybook).filter(GenrePlaybook.genre == pb.genre).update({"is_active": False})
            db.add(GenrePlaybook(genre=pb.genre, version=pb.version, content=data, checksum=_checksum(data)))
        elif exists.checksum != _checksum(data):
            log.error("playbook %s %s changed without a version bump; bump `version`", pb.genre, pb.version)
    db.commit()
