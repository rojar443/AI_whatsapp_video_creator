"""Provider-independent agent interface.

The agent proposes; the conversation state machine decides. Outputs are parsed into these
types and re-validated against app.conversation.preferences before use. No agent method
can start a job, touch the database, run commands or read arbitrary files.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel, Field

from app.conversation import preferences as prefs_mod
from app.pipeline.contracts import EditContext, PlanItem, ScriptOutput, Segment, SegmentScore

INTENTS = [
    "create_reel",  # wants a reel / describes one
    "done_uploading",  # "that's all", "done"
    "provide_info",  # answers a question / adds preferences
    "confirm",  # approves the plan (only honoured with an explicit yes/button)
    "modify",  # change preferences or the delivered draft
    "approve",  # happy with the delivered reel
    "cancel", "status", "help", "greeting", "new_project", "retry", "resend_link", "register",
    "off_topic",  # general chat — declined (Meta policy: no general-purpose assistant)
    "other",
]


@dataclass
class AgentContext:
    state: str  # project status or "none"
    pending: str | None = None  # what we just asked
    prefs: dict[str, Any] = field(default_factory=dict)
    media_summary: str = ""  # "4 clips, 2 photos"
    recent: list[tuple[str, str]] = field(default_factory=list)


class MessageAnalysis(BaseModel):
    intent: str = "other"
    confidence: float = Field(default=0.5, ge=0, le=1)
    fields: dict[str, Any] = Field(default_factory=dict)
    unsupported: list[str] = Field(default_factory=list)
    done: bool = False  # user signalled the upload batch is complete ("that's all")

    def sanitized(self) -> MessageAnalysis:
        fields = {}
        for k, v in self.fields.items():
            if k in prefs_mod.ALL_FIELDS:
                cv = prefs_mod.coerce_field(k, v)
                if cv is not None:
                    fields[k] = cv
        unsupported = [u for u in (prefs_mod.clean_text(x, 160) for x in self.unsupported[:5]) if u]
        return MessageAnalysis(intent=self.intent if self.intent in INTENTS else "other",
                               confidence=self.confidence, fields=fields, unsupported=unsupported,
                               done=self.done or self.intent == "done_uploading")


@dataclass
class UsageInfo:
    provider: str
    model: str | None
    input_tokens: int = 0
    output_tokens: int = 0
    task: str | None = None


# Collects token usage from any agent call made while set (e.g. during a pipeline run).
USAGE_SINK: ContextVar[list | None] = ContextVar("usage_sink", default=None)


class AgentError(Exception):
    pass


class AgentProvider(ABC):
    name = "base"
    is_llm = False
    supports_vision = False

    def __init__(self, models: dict[str, str | None] | None = None):
        self.models = models or {}
        self.last_usage: list[UsageInfo] = []

    @abstractmethod
    def analyze_message(self, text: str, ctx: AgentContext) -> MessageAnalysis:
        """Intent + proposed preference values for one user message."""

    def extract_preferences(self, text: str, ctx: AgentContext) -> MessageAnalysis:
        return self.analyze_message(text, ctx).sanitized()

    def score_moments(self, segments: list[Segment], frames: dict[str, list[bytes]],
                      context: EditContext) -> list[SegmentScore]:
        raise AgentError(f"{self.name} has no vision support")

    @abstractmethod
    def write_script(self, items: list[PlanItem], total_seconds: float, context: EditContext) -> ScriptOutput:
        """Narration script, one line per selected moment."""

    def generate_response(self, purpose: str, facts: dict[str, Any]) -> str:
        from app.conversation import replies

        return replies.render(purpose, facts)

    def health(self) -> dict[str, Any]:
        return {"ok": True, "provider": self.name, "models": self.models}
