"""Provider registry.

Resolution order for each category/task:
  1. provider_configurations row (set by an admin in the dashboard) — names/models only
  2. environment variables (AGENT_PROVIDER, AGENT_MODEL_<TASK>, VIDEO_PROVIDER, …)
Secrets always come from the environment. Business logic asks the registry for an
interface (AgentProvider, VideoProvider, …) and never imports a vendor module directly.
"""
from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agents.base import TASKS, AgentProvider
from app.config import get_settings
from app.models import ProviderConfiguration

log = logging.getLogger(__name__)

AGENT_PROVIDERS = ["rules", "anthropic", "openai", "openai_compatible", "gemini", "agenticorg"]
VIDEO_PROVIDERS = ["mock", "stock"]
TTS_PROVIDERS = ["mock", "espeak", "openai"]
TRANSCRIPTION_PROVIDERS = ["mock", "openai"]
CATEGORIES = {
    "agent": AGENT_PROVIDERS,
    "video": VIDEO_PROVIDERS,
    "tts": TTS_PROVIDERS,
    "transcription": TRANSCRIPTION_PROVIDERS,
}

_agent_factories: dict[str, Callable[[dict[str, str | None]], AgentProvider]] = {}
_overrides: dict[str, AgentProvider] = {}  # tests can inject fakes by name


def register_agent(name: str, factory: Callable[[dict[str, str | None]], AgentProvider]) -> None:
    _agent_factories[name] = factory


def _builtin_agent(name: str, models: dict[str, str | None]) -> AgentProvider:
    s = get_settings()
    if name in _overrides:
        return _overrides[name]
    if name in _agent_factories:
        return _agent_factories[name](models)
    if name == "rules":
        from app.agents.rules import RulesAgentProvider

        return RulesAgentProvider(models)
    if name == "agenticorg":
        from app.agents.agenticorg import AgenticOrgAgentProvider

        return AgenticOrgAgentProvider(
            models, api_key=s.agenticorg_api_key, agent_name=s.agenticorg_agent_name,
            base_url=s.agenticorg_base_url, company_id=s.agenticorg_company_id,
            action=s.agenticorg_action, timeout=s.agent_timeout_seconds,
        )
    from app.agents import llm

    if name == "anthropic":
        return llm.AnthropicAgentProvider(models, api_key=s.anthropic_api_key, timeout=s.agent_timeout_seconds)
    if name == "openai":
        if not s.openai_api_key:
            raise ValueError("OPENAI_API_KEY is required for the openai agent provider")
        return llm.OpenAIAgentProvider(models, api_key=s.openai_api_key, timeout=s.agent_timeout_seconds)
    if name == "openai_compatible":
        if not s.openai_base_url:
            raise ValueError("OPENAI_BASE_URL is required for openai_compatible")
        return llm.OpenAIAgentProvider(models, api_key=s.openai_api_key, base_url=s.openai_base_url,
                                       timeout=s.agent_timeout_seconds, strict_schema=False,
                                       name="openai_compatible")
    if name == "gemini":
        return llm.GeminiAgentProvider(models, api_key=s.gemini_api_key)
    raise ValueError(f"unknown agent provider '{name}'")


def _db_config(db: Session | None, category: str, task: str = "default") -> ProviderConfiguration | None:
    if db is None:
        return None
    return db.scalar(select(ProviderConfiguration).where(
        ProviderConfiguration.category == category, ProviderConfiguration.task == task))


def resolve(db: Session | None, category: str) -> dict[str, Any]:
    """Return {'provider': name, 'models': {task: model}, 'source': 'db'|'env'}."""
    s = get_settings()
    row = _db_config(db, category)
    env_provider = {"agent": s.agent_provider, "video": s.video_provider, "tts": s.tts_provider,
                    "transcription": s.transcription_provider}[category]
    provider = row.provider if row else env_provider
    models: dict[str, str | None] = {}
    if category == "agent":
        env_models = {"default": s.agent_model, "analysis": s.agent_model_analysis,
                      "response": s.agent_model_response, "script": s.agent_model_script}
        same_provider = provider == env_provider
        for task in ("default", *TASKS):
            trow = _db_config(db, "agent", task) if task != "default" else row
            if trow and trow.model:
                models[task] = trow.model
            elif same_provider and env_models.get(task):
                models[task] = env_models[task]
    return {"provider": provider, "models": models, "source": "db" if row else "env"}


def get_agent(db: Session | None) -> AgentProvider:
    """The configured agent, or the rules agent if it can't be constructed."""
    cfg = resolve(db, "agent")
    try:
        return _builtin_agent(cfg["provider"], cfg["models"])
    except Exception as e:  # noqa: BLE001
        log.error("agent provider %s unavailable (%s); falling back to rules", cfg["provider"], e)
        from app.agents.rules import RulesAgentProvider

        return RulesAgentProvider()


def build_agent(name: str, models: dict[str, str | None] | None = None) -> AgentProvider:
    return _builtin_agent(name, models or {})


def get_tts(db: Session | None):
    from app.tts import MockTTSProvider, get_tts as _get

    name = resolve(db, "tts")["provider"]
    try:
        return _get(name)
    except Exception as e:  # noqa: BLE001
        log.error("tts provider %s unavailable (%s); using mock", name, e)
        return MockTTSProvider(get_settings().ffmpeg_path)


def get_video_provider(db: Session | None):
    from app.video.mock import MockVideoProvider
    from app.video.stock import StockFootageProvider

    s = get_settings()
    name = resolve(db, "video")["provider"]
    tts = get_tts(db)
    writer = get_agent(db)
    if name == "stock":
        return StockFootageProvider(tts, writer, api_key=s.pexels_api_key)
    return MockVideoProvider(tts, writer)


def get_transcriber(db: Session | None):
    from app.transcription import get_transcriber as _get

    return _get(resolve(db, "transcription")["provider"])


_messaging_override = None


def set_messaging(provider) -> None:
    global _messaging_override
    _messaging_override = provider


def get_messaging():
    if _messaging_override is not None:
        return _messaging_override
    s = get_settings()
    if s.messaging_provider == "meta":
        from app.messaging.meta_cloud import MetaCloudProvider

        return MetaCloudProvider(s.whatsapp_access_token or "", s.whatsapp_phone_number_id or "",
                                 s.whatsapp_api_version)
    from app.messaging.dev import DevMessagingProvider

    return DevMessagingProvider()


def health_report(db: Session) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for category in ("agent", "video", "tts", "transcription"):
        cfg = resolve(db, category)
        try:
            if category == "agent":
                h = _builtin_agent(cfg["provider"], cfg["models"]).health()
            elif category == "video":
                h = get_video_provider(db).health()
            elif category == "tts":
                from app.tts import get_tts as _t

                p = _t(cfg["provider"])
                h = {"ok": True, "provider": p.name, "mock": p.is_mock}
            else:
                p = get_transcriber(db)
                h = {"ok": True, "provider": p.name}
        except Exception as e:  # noqa: BLE001
            h = {"ok": False, "provider": cfg["provider"], "error": str(e)[:200]}
        h["source"] = cfg["source"]
        out[category] = h
    try:
        out["messaging"] = get_messaging().health()
    except Exception as e:  # noqa: BLE001
        out["messaging"] = {"ok": False, "error": str(e)[:200]}
    return out
