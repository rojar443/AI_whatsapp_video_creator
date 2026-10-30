"""Collect safe AI fallback diagnostics independently of successful token usage."""
from contextvars import ContextVar
import re

from app.config import get_settings


ISSUE_SINK: ContextVar[list | None] = ContextVar("ai_issue_sink", default=None)


def safe_error(error: Exception | str) -> str:
    text = str(error) or (type(error).__name__ if isinstance(error, Exception) else "Unknown error")
    settings = get_settings()
    for field, value in settings.model_dump().items():
        if value and any(word in field for word in ("key", "secret", "token", "password")):
            text = text.replace(str(value), "[redacted]")
    text = re.sub(r"https?://[^\s<>\"']+", "[provider URL]", text)
    text = re.sub(r"(?i)(bearer\s+|(?:api[_-]?key|token|password)\s*[=:]\s*)[^\s,;]+",
                  r"\1[redacted]", text)
    text = re.sub(r"\b(?:AIza[\w-]+|sk-[\w-]+)\b", "[redacted]", text)
    return re.sub(r"[\x00-\x1f\x7f]", " ", text)[:1000]


def issue(task: str, agent, error: Exception | str, code: str = "request_failed") -> dict:
    settings = get_settings()
    configured_model = {"vision": settings.ai_model_vision, "script": settings.ai_model_script}.get(task) or settings.ai_model
    return {"task": task, "provider": settings.ai_provider if agent.name == "rules" else agent.name,
            "model": agent.models.get(task) or agent.models.get("default") or configured_model,
            "diagnostic": {"code": code, "error": safe_error(error),
                           "fallback": "heuristic" if task == "vision" else "rules"}}


def report(task: str, agent, error: Exception | str, code: str = "request_failed") -> None:
    sink = ISSUE_SINK.get()
    if sink is not None:
        sink.append(issue(task, agent, error, code))


def unavailable(agent) -> str:
    return getattr(agent, "fallback_error", None) or (
        "AI_PROVIDER=rules: AI is disabled by configuration." if get_settings().ai_provider == "rules"
        else f"The active provider ({agent.name}) does not support this AI operation.")
