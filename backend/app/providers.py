"""Provider registry — environment-configured (AI_PROVIDER, AI_MODEL, TTS_PROVIDER, …).

Business logic asks for an interface and never imports a vendor module directly. If the
configured AI provider can't be built (missing key), the rules agent is used and the
health endpoint reports the problem.
"""
from __future__ import annotations

import logging
from typing import Any

from app.agents.base import AgentProvider
from app.config import get_settings

log = logging.getLogger(__name__)

AGENT_PROVIDERS = ["gemini", "rules", "openai", "openai_compatible", "anthropic", "agenticorg"]
_overrides: dict[str, Any] = {}  # tests inject fakes: "agent", "messaging", "tts", "transcriber"


def build_agent(name: str | None = None) -> AgentProvider:
    s = get_settings()
    name = name or ("agenticorg" if s.agent_provider == "agenticorg" else s.ai_provider)
    models = {"default": s.ai_model, "analysis": s.ai_model, "vision": s.ai_model_vision or s.ai_model,
              "script": s.ai_model_script or s.ai_model}
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

    if name == "gemini":
        return llm.GeminiAgentProvider(models, api_key=s.gemini_api_key, timeout=s.ai_timeout_seconds)
    if name == "openai":
        if not s.openai_api_key:
            raise ValueError("OPENAI_API_KEY is required for AI_PROVIDER=openai")
        return llm.OpenAIAgentProvider(models, api_key=s.openai_api_key, timeout=s.ai_timeout_seconds)
    if name == "openai_compatible":
        if not s.openai_base_url:
            raise ValueError("OPENAI_BASE_URL is required for AI_PROVIDER=openai_compatible")
        return llm.OpenAIAgentProvider(models, api_key=s.openai_api_key, base_url=s.openai_base_url,
                                       timeout=s.ai_timeout_seconds, name="openai_compatible")
    if name == "anthropic":
        return llm.AnthropicAgentProvider(models, api_key=s.anthropic_api_key, timeout=s.ai_timeout_seconds)
    raise ValueError(f"unknown AI_PROVIDER '{name}' (choose from {', '.join(AGENT_PROVIDERS)})")


def get_agent() -> AgentProvider:
    if "agent" in _overrides:
        return _overrides["agent"]
    try:
        return build_agent()
    except Exception as e:  # noqa: BLE001
        log.error("AI provider %s unavailable (%s); using rules agent", get_settings().ai_provider, e)
        from app.agents.rules import RulesAgentProvider

        return RulesAgentProvider()


def get_transcriber():
    if "transcriber" in _overrides:
        return _overrides["transcriber"]
    from app.transcription import GeminiTranscriptionProvider, MockTranscriptionProvider

    s = get_settings()
    if s.transcription_provider == "gemini":
        try:
            return GeminiTranscriptionProvider()
        except Exception as e:  # noqa: BLE001
            log.error("gemini transcription unavailable (%s); using mock", e)
    return MockTranscriptionProvider()


def get_tts():
    if "tts" in _overrides:
        return _overrides["tts"]
    from app.tts import MockTTS, get_tts as _get

    try:
        return _get()
    except Exception as e:  # noqa: BLE001
        log.error("tts provider %s unavailable (%s); falling back", get_settings().tts_provider, e)
        try:
            return _get("espeak")
        except Exception:  # noqa: BLE001
            return MockTTS()


_telegram_cache: dict = {}


def get_telegram():
    """Telegram provider when TELEGRAM_BOT_TOKEN is set (one shared client), else None."""
    if "telegram" in _overrides:
        return _overrides["telegram"]
    token = get_settings().telegram_bot_token
    if not token:
        return None
    if _telegram_cache.get("token") != token:
        from app.messaging.telegram import TelegramProvider

        _telegram_cache.update(token=token, provider=TelegramProvider(token))
    return _telegram_cache["provider"]


def get_messaging():
    """WhatsApp (or dev) provider, wrapped in a router that also serves Telegram chats when configured."""
    if "messaging" in _overrides:
        return _overrides["messaging"]
    s = get_settings()
    if s.messaging_provider == "meta":
        from app.messaging.meta_cloud import MetaCloudProvider

        primary = MetaCloudProvider(s.whatsapp_access_token or "", s.whatsapp_phone_number_id or "",
                                    s.whatsapp_api_version)
    else:
        from app.messaging.dev import DevMessagingProvider

        primary = DevMessagingProvider()
    from app.messaging.router import MessagingRouter

    return MessagingRouter(primary, get_telegram())


def health_report() -> dict[str, Any]:
    from app.media import ffmpeg
    from app.pipeline import registry

    s = get_settings()
    out: dict[str, Any] = {}
    try:
        out["ai"] = build_agent().health()
    except Exception as e:  # noqa: BLE001
        out["ai"] = {"ok": False, "provider": s.ai_provider, "error": str(e)[:200]}
    try:
        t = get_tts()
        out["tts"] = {"ok": t.name == s.tts_provider, "provider": t.name, "configured": s.tts_provider}
    except Exception as e:  # noqa: BLE001
        out["tts"] = {"ok": False, "error": str(e)[:200]}
    out["transcription"] = {"provider": get_transcriber().name, "configured": s.transcription_provider}
    try:
        out["messaging"] = get_messaging().health()
    except Exception as e:  # noqa: BLE001
        out["messaging"] = {"ok": False, "provider": s.messaging_provider, "error": str(e)[:200]}
    out["ffmpeg"] = {"ok": ffmpeg.available()}
    out["pipeline"] = registry.describe()
    return out
