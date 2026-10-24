"""AgenticOrg interpretation contract and handoff to the conversation engine."""
import json
from types import SimpleNamespace

import httpx
import pytest

from app import providers
from app.agents.agenticorg import AgenticOrgAgentProvider
from app.agents.base import AgentContext
from app.agents.rules import RulesAgentProvider
from app.config import get_settings


INTERPRETATION = {
    "intent": "create_reel", "confidence": 0.94,
    "fields": {"genre": "travel", "vibe": "funny", "duration": "30"},
    "unsupported": [], "done": False,
}


class FakeClient:
    def __init__(self, output=INTERPRETATION, status="completed", error=None):
        self.calls = []

        def run(name, **kwargs):
            self.calls.append((name, kwargs))
            if error:
                raise error
            return SimpleNamespace(status=status, output=output, confidence=0.8)

        self.agents = SimpleNamespace(run=run)


@pytest.mark.parametrize("as_text", [False, True])
def test_output_and_followup_context(as_text):
    fake = FakeClient(json.dumps(INTERPRETATION) if as_text else INTERPRETATION)
    agent = AgenticOrgAgentProvider(agent_name="video_interpreter", client=fake,
                                    company_id="company", action="interpret")
    ctx = AgentContext(state="awaiting_input", pending="preferences",
                       prefs={"genre": "travel"}, recent=[("assistant", "How long?")])
    result = agent.analyze_message("Thirty seconds please", ctx)
    assert result.fields == INTERPRETATION["fields"]
    name, kwargs = fake.calls[0]
    assert name == "video_interpreter"
    assert kwargs == {
        "action": "interpret", "company_id": "company",
        "inputs": {"task": "Thirty seconds please"}, "context": {
            "state": "awaiting_input", "pending": "preferences", "media_summary": "",
            "preferences": {"genre": "travel"},
            "recent_messages": [{"role": "assistant", "text": "How long?"}],
        },
    }


def test_sanitizes_fields_and_uses_run_confidence():
    fake = FakeClient({"intent": "grant_admin", "fields": {
        "genre": "travel", "user_id": "someone-else", "vibe": "invalid",
    }})
    agent = AgenticOrgAgentProvider(agent_name="video_interpreter", client=fake)
    result = agent.analyze_message("hello", AgentContext(state="none"))
    assert result.intent == "other"
    assert result.confidence == 0.8
    assert result.fields == {"genre": "travel"}


@pytest.mark.parametrize("output", [None, {}, [], "bad json", '{"intent": "create_reel"}',
                                   {"intent": "create_reel", "fields": []},
                                   dict(INTERPRETATION, confidence=2)])
def test_invalid_output_uses_original_message(output):
    agent = AgenticOrgAgentProvider(agent_name="video_interpreter", client=FakeClient(output))
    text = "Create a cinematic travel reel"
    ctx = AgentContext(state="none")
    assert agent.analyze_message(text, ctx) == RulesAgentProvider().analyze_message(text, ctx).sanitized()


@pytest.mark.parametrize("status", ["failed", "hitl_triggered", "budget_exceeded", "running", ""])
def test_only_completed_runs_are_consumed(status):
    agent = AgenticOrgAgentProvider(agent_name="video_interpreter", client=FakeClient(status=status))
    result = agent.analyze_message("Create a cinematic travel reel", AgentContext(state="none"))
    assert result.fields["vibe"] == "cinematic"  # discard the remote funny-vibe output


@pytest.mark.parametrize("error", [TimeoutError("private error details"), RuntimeError("private error details"),
    httpx.HTTPStatusError("private error details", request=httpx.Request("POST", "https://agent.example"),
                          response=httpx.Response(401))])
def test_api_errors_are_suppressed_and_followup_context_is_preserved(error, caplog):
    agent = AgenticOrgAgentProvider(agent_name="video_interpreter", client=FakeClient(error=error))
    ctx = AgentContext(state="awaiting_input", pending="preferences", prefs={"genre": "travel"})
    with caplog.at_level("INFO", logger="app.agents.agenticorg"):
        result = agent.analyze_message("30 seconds with no captions", ctx)
    assert result.fields["duration"] == "30"
    assert result.fields["captions"] is False
    assert "private error details" not in caplog.text


def test_configuration_requires_key_and_agent_name():
    with pytest.raises(ValueError, match="AGENTICORG_AGENT_NAME"):
        AgenticOrgAgentProvider(api_key="test")
    with pytest.raises(ValueError, match="AGENTICORG_API_KEY"):
        AgenticOrgAgentProvider(agent_name="video_interpreter")


@pytest.mark.parametrize("endpoint", [None, "https://agent.example"])
def test_registry_uses_uuid_route_with_real_sdk_and_mock_http(monkeypatch, endpoint):
    pytest.importorskip("agenticorg")
    settings = get_settings()
    for key, value in {
        "agent_provider": "agenticorg", "agenticorg_api_key": "test-key",
        "agenticorg_agent_name": "888f7361-3216-43f3-8380-df3052001f6e", "agenticorg_company_id": None,
        "agenticorg_base_url": endpoint, "agent_timeout_seconds": 7.0,
    }.items():
        monkeypatch.setattr(settings, key, value)
    requests = []
    clients = []
    original_client = httpx.Client

    def handle(request):
        requests.append(request)
        return httpx.Response(200, json={"run_id": "run-1", "status": "completed",
                                         "output": INTERPRETATION, "confidence": 0.94})

    def create_client(**kwargs):
        client = original_client(**kwargs, transport=httpx.MockTransport(handle))
        clients.append(client)
        return client

    monkeypatch.setattr("agenticorg.client.httpx.Client", create_client)
    agent = providers.get_agent()
    assert agent.name == "agenticorg"
    assert agent.analyze_message("make something", AgentContext(state="none")).fields["genre"] == "travel"
    expected_base = endpoint or "https://agenticorg.hackathon.pinelabs.com"
    assert str(requests[0].url) == expected_base + "/api/v1/agents/888f7361-3216-43f3-8380-df3052001f6e/run"
    assert requests[0].headers["authorization"] == "Bearer test-key"
    payload = json.loads(requests[0].content)
    assert "company_id" not in payload
    assert "agent_type" not in payload
    assert payload["inputs"] == {"task": "make something"}
    assert payload["context"]["state"] == "none"
    assert clients[0].timeout.read == 7.0
    assert clients[0].is_closed

