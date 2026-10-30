"""Provider registry, health report, Celery wiring and the Telegram long-poll loop."""
import pytest

from app import providers, telegram_poller
from app.agents import llm
from app.agents.rules import RulesAgentProvider
from app.config import get_settings
from app.messaging.base import SendError
from app.messaging.dev import DevMessagingProvider
from app.messaging.meta_cloud import MetaCloudProvider
from app.messaging.router import MessagingRouter
from app.models import Message
from app.transcription import MockTranscriptionProvider
from app.tts import MockTTS


@pytest.fixture
def settings(monkeypatch):
    s = get_settings()

    def set_(**kw):
        for k, v in kw.items():
            monkeypatch.setattr(s, k, v)
        return s

    providers._telegram_cache.clear()
    yield set_
    providers._telegram_cache.clear()


# ------------------------------------------------------------------ build_agent
def test_build_agent_rules():
    assert isinstance(providers.build_agent("rules"), RulesAgentProvider)


def test_build_agent_models_mapping(settings):
    settings(ai_model="base", ai_model_vision=None, ai_model_script="writer", openai_api_key="k")
    a = providers.build_agent("openai")
    assert a.models == {"default": "base", "analysis": "base", "vision": "base", "script": "writer"}


def test_build_agent_gemini(settings, monkeypatch):
    seen = {}
    monkeypatch.setattr(llm.GeminiAgentProvider, "__init__",
                        lambda self, models, api_key=None, timeout=60: seen.update(key=api_key, t=timeout))
    settings(gemini_api_key="g-key", ai_timeout_seconds=12)
    assert isinstance(providers.build_agent("gemini"), llm.GeminiAgentProvider)
    assert seen == {"key": "g-key", "t": 12}


def test_build_agent_gemini_without_key(settings):
    settings(gemini_api_key=None)
    with pytest.raises(ValueError, match="GEMINI_API_KEY"):
        providers.build_agent("gemini")


def test_build_agent_openai_requires_key(settings):
    settings(openai_api_key=None)
    with pytest.raises(ValueError, match="OPENAI_API_KEY"):
        providers.build_agent("openai")


def test_build_agent_openai_compatible(settings):
    settings(openai_base_url=None)
    with pytest.raises(ValueError, match="OPENAI_BASE_URL"):
        providers.build_agent("openai_compatible")
    settings(openai_base_url="http://ollama:11434/v1/", openai_api_key=None)
    a = providers.build_agent("openai_compatible")
    assert a.name == "openai_compatible" and a.base == "http://ollama:11434/v1"


def test_build_agent_anthropic(settings, monkeypatch):
    seen = {}
    monkeypatch.setattr(llm.AnthropicAgentProvider, "__init__",
                        lambda self, models, api_key=None, timeout=60: seen.update(key=api_key))
    settings(anthropic_api_key="a-key")
    assert isinstance(providers.build_agent("anthropic"), llm.AnthropicAgentProvider)
    assert seen["key"] == "a-key"


def test_build_agent_unknown():
    with pytest.raises(ValueError, match="unknown AI_PROVIDER 'nope'"):
        providers.build_agent("nope")


def test_build_agent_uses_settings_default(settings):
    settings(ai_provider="rules")
    assert isinstance(providers.build_agent(), RulesAgentProvider)


# ------------------------------------------------------------------ get_agent / transcriber / tts
def test_get_agent_override():
    fake = object()
    providers._overrides["agent"] = fake
    assert providers.get_agent() is fake


def test_get_agent_falls_back_to_rules(settings):
    settings(ai_provider="openai", openai_api_key=None)
    assert isinstance(providers.get_agent(), RulesAgentProvider)


def test_get_transcriber_override_and_mock(settings):
    providers._overrides["transcriber"] = "T"
    assert providers.get_transcriber() == "T"
    providers._overrides.clear()
    settings(transcription_provider="mock")
    assert isinstance(providers.get_transcriber(), MockTranscriptionProvider)


def test_get_transcriber_gemini_falls_back(settings):
    settings(transcription_provider="gemini", gemini_api_key=None)
    assert isinstance(providers.get_transcriber(), MockTranscriptionProvider)


def test_get_transcriber_gemini(settings, monkeypatch):
    from app import transcription

    monkeypatch.setattr(transcription.GeminiTranscriptionProvider, "__init__", lambda self, agent=None: None)
    settings(transcription_provider="gemini")
    assert isinstance(providers.get_transcriber(), transcription.GeminiTranscriptionProvider)


def test_get_tts_override_and_default():
    providers._overrides["tts"] = "X"
    assert providers.get_tts() == "X"
    providers._overrides.clear()
    assert isinstance(providers.get_tts(), MockTTS)


def test_get_tts_falls_back_to_espeak_then_mock(monkeypatch):
    from app import tts

    calls = []

    def broken(name=None):
        calls.append(name)
        if name is None:
            raise RuntimeError("no key")
        return "espeak-instance"

    monkeypatch.setattr(tts, "get_tts", broken)
    assert providers.get_tts() == "espeak-instance" and calls == [None, "espeak"]

    def always_broken(name=None):
        raise RuntimeError("x")

    monkeypatch.setattr(tts, "get_tts", always_broken)
    assert isinstance(providers.get_tts(), MockTTS)


# ------------------------------------------------------------------ telegram / messaging
def test_get_telegram_none_without_token(settings):
    settings(telegram_bot_token=None)
    assert providers.get_telegram() is None


def test_get_telegram_cached_per_token(settings):
    settings(telegram_bot_token="1:a")
    a = providers.get_telegram()
    assert providers.get_telegram() is a
    settings(telegram_bot_token="2:b")
    assert providers.get_telegram() is not a


def test_get_telegram_override():
    providers._overrides["telegram"] = "tg"
    assert providers.get_telegram() == "tg"


def test_get_messaging_dev(settings):
    settings(messaging_provider="dev", telegram_bot_token=None)
    m = providers.get_messaging()
    assert isinstance(m, MessagingRouter) and isinstance(m.primary, DevMessagingProvider)


def test_get_messaging_meta(settings):
    settings(messaging_provider="meta", whatsapp_access_token="tok", whatsapp_phone_number_id="123",
             telegram_bot_token=None)
    assert isinstance(providers.get_messaging().primary, MetaCloudProvider)


def test_get_messaging_override():
    providers._overrides["messaging"] = "M"
    assert providers.get_messaging() == "M"


# ------------------------------------------------------------------ health_report
def test_health_report_ok(settings):
    settings(ai_provider="rules", tts_provider="mock", telegram_bot_token=None)
    r = providers.health_report()
    assert r["ai"]["ok"] is True
    assert r["tts"] == {"ok": True, "provider": "mock", "configured": "mock"}
    assert r["transcription"]["provider"] == "mock"
    assert "ok" in r["ffmpeg"] and "pipeline" in r and "messaging" in r


def test_health_report_failures(settings, monkeypatch):
    settings(ai_provider="openai", openai_api_key=None, tts_provider="elevenlabs")
    monkeypatch.setattr(providers, "get_tts", lambda: (_ for _ in ()).throw(RuntimeError("tts down")))
    monkeypatch.setattr(providers, "get_messaging", lambda: (_ for _ in ()).throw(RuntimeError("wa down")))
    r = providers.health_report()
    assert r["ai"] == {"ok": False, "provider": "openai", "error": "OPENAI_API_KEY is required for AI_PROVIDER=openai"}
    assert r["tts"] == {"ok": False, "error": "tts down"}
    assert r["messaging"]["ok"] is False and r["messaging"]["error"] == "wa down"


def test_health_report_tts_mismatch(settings):
    settings(ai_provider="rules", tts_provider="espeak")
    providers._overrides["tts"] = MockTTS()
    assert providers.health_report()["tts"]["ok"] is False


# ------------------------------------------------------------------ celery wiring
def test_celery_config_and_tasks(monkeypatch):
    from app.jobs import celery_app as mod
    from app.jobs import dispatch

    conf = mod.celery_app.conf
    assert conf.task_acks_late and conf.worker_prefetch_multiplier == 1
    assert {"sweep", "purge"} <= set(conf.beat_schedule)
    assert {"reelchat.run_default", "reelchat.run_media"} <= set(mod.celery_app.tasks)

    sent, ran = [], []
    monkeypatch.setattr(dispatch, "_send", lambda *a: sent.append(a))

    def fake_execute(name, args, attempt, retry):
        ran.append((name, args, attempt))
        retry(5)

    monkeypatch.setattr(dispatch, "execute", fake_execute)
    mod.run_default.run("sweep", [1], 2)
    mod.run_media.run("render", ["p"])
    assert ran == [("sweep", [1], 2), ("render", ["p"], 1)]
    assert sent == [("sweep", (1,), 5, 3), ("render", ("p",), 5, 2)]
    mod._init()  # worker_process_init hook re-applies logging


# ------------------------------------------------------------------ telegram poller
class FakeTG:
    def __init__(self, batches):
        self.batches = list(batches)
        self.answered, self.offsets = [], []

    def health(self):
        return {"ok": True, "bot": "@reelbot"}

    def answer_callback(self, cid):
        self.answered.append(cid)

    def get_updates(self, offset, timeout):
        self.offsets.append(offset)
        if not self.batches:
            telegram_poller._stop = True
            return []
        b = self.batches.pop(0)
        if isinstance(b, Exception):
            raise b
        return b


def text_update(uid, text="hi", chat=42):
    return {"update_id": uid, "message": {"message_id": uid, "date": 1700000000, "text": text,
                                          "chat": {"id": chat, "type": "private"}, "from": {"first_name": "A"}}}


def test_handle_update_stores_message_once(db):
    tg = FakeTG([])
    assert telegram_poller.handle_update(db, tg, text_update(1)) is True
    assert telegram_poller.handle_update(db, tg, text_update(1)) is False
    assert db.query(Message).count() == 1


def test_handle_update_ignores_non_private_and_empty(db):
    upd = text_update(2)
    upd["message"]["chat"]["type"] = "group"
    assert telegram_poller.handle_update(db, None, upd) is False
    assert telegram_poller.handle_update(db, None, {"update_id": 3}) is False


def test_handle_update_answers_callbacks(db):
    tg = FakeTG([])
    cb = {"update_id": 4, "callback_query": {"id": "cb1", "data": "confirm", "from": {"first_name": "A"},
                                             "message": {"chat": {"id": 42, "type": "private"}}}}
    assert telegram_poller.handle_update(db, tg, cb) is True
    assert tg.answered == ["cb1"]
    # callback without data is still acknowledged but not stored
    cb2 = {"update_id": 5, "callback_query": {"id": "cb2", "message": {"chat": {"id": 42, "type": "private"}}}}
    assert telegram_poller.handle_update(db, tg, cb2) is False and tg.answered[-1] == "cb2"


@pytest.fixture
def poller(monkeypatch):
    monkeypatch.setattr(telegram_poller, "_stop", False)
    monkeypatch.setattr(telegram_poller.signal, "signal", lambda *a: None)
    sleeps = []
    monkeypatch.setattr(telegram_poller.time, "sleep", sleeps.append)

    def run(tg):
        providers._overrides["telegram"] = tg
        telegram_poller.main()
        return sleeps

    return run


def test_main_requires_token(monkeypatch, settings):
    settings(telegram_bot_token=None)
    with pytest.raises(SystemExit, match="TELEGRAM_BOT_TOKEN"):
        telegram_poller.main()


def test_main_loop_advances_offset_and_backs_off(poller, db, monkeypatch):
    tg = FakeTG([SendError("down"), SendError("down"), [text_update(10), text_update(11)], [text_update(12)]])
    sleeps = poller(tg)
    assert sleeps == [1.0, 2.0]
    assert tg.offsets == [None, None, None, 12, 13]
    assert db.query(Message).count() == 3


def test_main_backoff_caps_at_30(poller):
    sleeps = poller(FakeTG([SendError("x")] * 7))
    assert sleeps == [1.0, 2.0, 4.0, 8.0, 16.0, 30.0, 30.0]


def test_main_survives_bad_update(poller, monkeypatch, db):
    real = telegram_poller.handle_update

    def flaky(db_, tg, u):
        if u["update_id"] == 20:
            raise RuntimeError("bad")
        return real(db_, tg, u)

    monkeypatch.setattr(telegram_poller, "handle_update", flaky)
    tg = FakeTG([[text_update(20), text_update(21)]])
    poller(tg)
    assert tg.offsets[-1] == 22 and db.query(Message).count() == 1
