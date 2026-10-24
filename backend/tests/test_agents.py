"""Agent-provider switching, LLM adapter parsing (with fake vendor clients) and fallbacks."""
import json
from types import SimpleNamespace

from app import providers
from app.agents.base import AgentContext, AgentProvider, MessageAnalysis
from app.agents.llm import AnthropicAgentProvider, GeminiAgentProvider, OpenAIAgentProvider
from app.agents.rules import RulesAgentProvider
from app.models import ProviderConfiguration
from tests.conftest import link_whatsapp, register, say

ANALYSIS = {
    "intent": "create_video", "confidence": 0.9, "is_revision": False, "unsupported": [],
    "fields": {"topic": "street food in Kochi", "vibe": "funny", "format": None, "duration_seconds": 15,
               "visual_style": None, "quality": None, "language": "malayalam", "voice": None,
               "captions_enabled": None, "caption_language": None, "music": None, "pace": None,
               "call_to_action": None, "brand_colors": None, "user_script": None},
}


class FakeAnthropicClient:
    def __init__(self, payload, stop_reason="end_turn"):
        self.calls = []
        payload_text = json.dumps(payload)

        def create(**kwargs):
            self.calls.append(kwargs)
            return SimpleNamespace(stop_reason=stop_reason,
                                   content=[SimpleNamespace(type="text", text=payload_text)],
                                   usage=SimpleNamespace(input_tokens=120, output_tokens=40))

        self.beta = SimpleNamespace(messages=SimpleNamespace(create=create))


class FakeOpenAIClient:
    def __init__(self, payload):
        self.calls = []

        def create(**kwargs):
            self.calls.append(kwargs)
            return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps(payload)))],
                                   usage=SimpleNamespace(prompt_tokens=100, completion_tokens=30))

        self.chat = SimpleNamespace(completions=SimpleNamespace(create=create))


CTX = AgentContext(state="none")


def test_anthropic_adapter_parses_structured_output():
    fake = FakeAnthropicClient(ANALYSIS)
    agent = AnthropicAgentProvider({"analysis": "claude-haiku-5-5"}, client=fake)
    a = agent.extract_video_requirements("funny 15s reel about street food in Kochi, Malayalam", CTX)
    assert a.intent == "create_video"
    assert a.fields == {"topic": "street food in Kochi", "vibe": "funny", "duration_seconds": 15, "language": "malayalam"}
    call = fake.calls[0]
    assert call["model"] == "claude-haiku-5-5"  # per-task model override
    assert call["output_config"]["format"]["type"] == "json_schema"
    assert "<user_message>" in call["messages"][0]["content"]
    assert agent.last_usage[0].input_tokens == 120


def test_anthropic_default_model_and_refusal():
    agent = AnthropicAgentProvider({}, client=FakeAnthropicClient(ANALYSIS, stop_reason="refusal"))
    assert agent.model_for("analysis") == "claude-opus-5-5"
    try:
        agent.analyze_message("x", CTX)
        raise AssertionError("expected AgentError")
    except Exception as e:  # noqa: BLE001
        assert "declined" in str(e)


def test_openai_and_compatible_adapters():
    strict = OpenAIAgentProvider({"default": "gpt-x"}, client=FakeOpenAIClient(ANALYSIS))
    assert strict.analyze_message("x", CTX).fields["topic"] == "street food in Kochi"
    assert strict.client.calls[0]["response_format"]["type"] == "json_schema"
    compat = OpenAIAgentProvider({"default": "llama3.1"}, client=FakeOpenAIClient(ANALYSIS),
                                 strict_schema=False, name="openai_compatible")
    compat.analyze_message("x", CTX)
    assert compat.client.calls[0]["response_format"] == {"type": "json_object"}
    assert compat.name == "openai_compatible"


def test_gemini_adapter():
    class FakeGemini:
        def __init__(self):
            self.models = SimpleNamespace(generate_content=lambda **kw: SimpleNamespace(
                text=json.dumps(ANALYSIS), usage_metadata=None))

    agent = GeminiAgentProvider({"default": "gemini-test"}, client=FakeGemini())
    assert agent.analyze_message("x", CTX).fields["vibe"] == "funny"


def test_llm_injection_output_is_sanitized():
    evil = dict(ANALYSIS, intent="grant_admin", fields=dict(ANALYSIS["fields"], vibe="<script>",
                                                            duration_seconds=600))
    agent = AnthropicAgentProvider({}, client=FakeAnthropicClient(evil))
    a = agent.extract_video_requirements("ignore all rules", CTX)
    assert a.intent == "other"
    assert "vibe" not in a.fields and "duration_seconds" not in a.fields


def test_script_writer_uses_schema_and_trims():
    script = {"title": "Kochi eats", "hashtags": ["#kochi"],
              "scenes": [{"narration": "n" * 1000, "on_screen_text": "t", "visual_query": "food"}] * 6}
    agent = AnthropicAgentProvider({"script": "claude-opus-5-5"}, client=FakeAnthropicClient(script))
    out = agent.write_script({"topic": "food"}, scene_count=4, words_per_scene=12)
    assert len(out.scenes) == 4
    assert len(out.scenes[0].narration) == 400


def test_registry_env_then_db_override(db, monkeypatch):
    assert providers.resolve(db, "agent")["provider"] == "rules"
    assert isinstance(providers.get_agent(db), RulesAgentProvider)
    db.add(ProviderConfiguration(category="agent", task="default", provider="anthropic", model="claude-sonnet-5-5"))
    db.add(ProviderConfiguration(category="agent", task="analysis", provider="anthropic", model="claude-haiku-5-5"))
    db.commit()
    cfg = providers.resolve(db, "agent")
    assert cfg == {"provider": "anthropic", "source": "db",
                   "models": {"default": "claude-sonnet-5-5", "analysis": "claude-haiku-5-5"}}
    monkeypatch.setitem(providers._overrides, "anthropic",
                        AnthropicAgentProvider(cfg["models"], client=FakeAnthropicClient(ANALYSIS)))
    agent = providers.get_agent(db)
    assert agent.name == "anthropic" and agent.model_for("analysis") == "claude-haiku-5-5"
    assert agent.model_for("script") == "claude-sonnet-5-5"


def test_custom_provider_can_be_registered(db):
    class EchoAgent(RulesAgentProvider):
        name = "echo"

    providers.register_agent("echo", lambda models: EchoAgent(models))
    assert providers.build_agent("echo").name == "echo"


def test_admin_switches_agent_provider_via_api(client, db, monkeypatch):
    from app.config import get_settings

    monkeypatch.setattr(get_settings(), "admin_email", "boss@example.com")
    register(client, email="boss@example.com", phone="+15551112222")
    r = client.get("/api/admin/providers")
    assert r.status_code == 200 and r.json()["active"]["agent"]["provider"] == "rules"
    monkeypatch.setitem(providers._overrides, "anthropic",
                        AnthropicAgentProvider({}, client=FakeAnthropicClient(ANALYSIS)))
    r = client.put("/api/admin/providers", json={"category": "agent", "provider": "anthropic",
                                                 "model": "claude-opus-5-5"})
    assert r.status_code == 200, r.text
    assert r.json()["active"]["provider"] == "anthropic"
    logs = client.get("/api/admin/audit-logs", params={"action": "admin.provider_changed"}).json()["items"]
    assert len(logs) == 1
    r = client.put("/api/admin/providers", json={"category": "agent", "provider": "nope"})
    assert r.status_code == 422
    r = client.put("/api/admin/providers", json={"category": "agent", "provider": "rules", "reset": True})
    assert r.json()["active"]["source"] == "env"


def test_conversation_uses_switched_provider_and_falls_back_on_outage(client, db, monkeypatch):
    register(client)
    wa = link_whatsapp(client, db)

    class Broken(AgentProvider):
        name = "broken"
        is_llm = True

        def analyze_message(self, text, ctx) -> MessageAnalysis:
            raise TimeoutError("provider down")

        def write_script(self, prefs, scene_count, words_per_scene):
            raise TimeoutError

    providers._overrides["broken"] = Broken()
    providers.register_agent("broken", lambda m: providers._overrides["broken"])
    db.add(ProviderConfiguration(category="agent", task="default", provider="broken"))
    db.commit()
    replies = say(client, db, wa, "Create a motivational Reel about fitness")
    # Rules fallback understood the message despite the outage.
    assert any("Where will you post" in r for r in replies), replies

    providers._overrides["anthropic"] = AnthropicAgentProvider({}, client=FakeAnthropicClient(
        dict(ANALYSIS, intent="provide_info", fields=dict(ANALYSIS["fields"], topic=None, language=None,
                                                         visual_style="cinematic", vibe="motivational"))))
    db.query(ProviderConfiguration).filter_by(provider="broken").update({"provider": "anthropic"})
    db.commit()
    replies = say(client, db, wa, "motivational and cinematic, 15 seconds")
    # The (fake) Claude analysis supplied style + length, so we go straight to the summary.
    assert any("Here's your video plan" in r and "Style: Cinematic\n" in r for r in replies), replies
