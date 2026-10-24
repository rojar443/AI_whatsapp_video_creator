"""Provider-independent agent interface.

The agent *proposes*; the conversation state machine *decides*. Agent outputs are parsed
into these types and every field value is re-validated by app.conversation.requirements
before use. No agent method can start a generation job or touch the database.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Literal

from pydantic import BaseModel, Field

from app.conversation import requirements as req

Intent = Literal[
    "create_video",  # wants a new video
    "provide_info",  # answering a question / adding preferences
    "confirm",  # approves the summary (only honoured with an explicit yes/button)
    "modify",  # change a previous requirement or a finished video
    "cancel",
    "status",
    "help",
    "greeting",
    "new_project",
    "retry",
    "resend_link",
    "other",
]
INTENTS: list[str] = list(Intent.__args__)  # type: ignore[attr-defined]

TASKS = ("analysis", "response", "script")


@dataclass
class AgentContext:
    state: str  # project status, or "none"
    pending_group: str | None = None  # question group we just asked
    prefs: dict[str, Any] = field(default_factory=dict)
    recent: list[tuple[str, str]] = field(default_factory=list)  # (role, text), oldest first


class MessageAnalysis(BaseModel):
    intent: str = "other"
    confidence: float = Field(default=0.5, ge=0, le=1)
    fields: dict[str, Any] = Field(default_factory=dict)  # unvalidated proposals
    unsupported: list[str] = Field(default_factory=list)  # requests we can't honour
    is_revision: bool = False

    def sanitized(self) -> MessageAnalysis:
        intent = self.intent if self.intent in INTENTS else "other"
        fields = {}
        for k, v in self.fields.items():
            if k in req.ALL_FIELDS:
                cv = req.coerce_field(k, v)
                if cv is not None:
                    fields[k] = cv
        unsupported = [req.clean_text(u, 160) for u in self.unsupported[:5]]
        return MessageAnalysis(
            intent=intent,
            confidence=self.confidence,
            fields=fields,
            unsupported=[u for u in unsupported if u],
            is_revision=self.is_revision,
        )


class Scene(BaseModel):
    narration: str
    on_screen_text: str
    visual_query: str  # English keywords for stock/image search


class Script(BaseModel):
    title: str
    scenes: list[Scene]
    hashtags: list[str] = Field(default_factory=list)


@dataclass
class UsageInfo:
    provider: str
    model: str | None
    input_tokens: int = 0
    output_tokens: int = 0


class AgentError(Exception):
    pass


class AgentProvider(ABC):
    """Swap implementations via the registry; business logic only sees this interface."""

    name: str = "base"
    is_llm: bool = False

    def __init__(self, models: dict[str, str | None] | None = None):
        self.models = models or {}
        self.last_usage: list[UsageInfo] = []

    # --- core operations -------------------------------------------------------------
    @abstractmethod
    def analyze_message(self, text: str, ctx: AgentContext) -> MessageAnalysis:
        """One pass that returns intent + extracted requirement proposals."""

    def identify_intent(self, text: str, ctx: AgentContext) -> tuple[str, float]:
        a = self.analyze_message(text, ctx).sanitized()
        return a.intent, a.confidence

    def extract_video_requirements(self, text: str, ctx: AgentContext) -> MessageAnalysis:
        return self.analyze_message(text, ctx).sanitized()

    def generate_response(self, purpose: str, ctx: AgentContext, facts: dict[str, Any]) -> str:
        from app.conversation import replies

        return replies.render(purpose, facts)

    def summarize_requirements(
        self, prefs: req.Prefs, credits_cost: int, balance: int, notes: list[str] | None = None
    ) -> str:
        # Deterministic on purpose: the user approves exactly what will be generated.
        return req.summarize(prefs, credits_cost, balance, notes)

    def validate_requirements(self, prefs: req.Prefs, capabilities: dict) -> req.ValidationResult:
        return req.validate(prefs, capabilities)

    @abstractmethod
    def write_script(self, prefs: dict[str, Any], scene_count: int, words_per_scene: int) -> Script:
        """Create the narration script for the video pipeline."""

    def health(self) -> dict[str, Any]:
        return {"ok": True, "provider": self.name, "models": self.models}
