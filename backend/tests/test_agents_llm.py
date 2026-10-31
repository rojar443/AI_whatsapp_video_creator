"""LLM agent adapters: shared prompt/validation logic plus each vendor's wire format, with fake clients."""
import json
from types import SimpleNamespace

import httpx
import pytest

from app.agents.base import USAGE_SINK, AgentContext, AgentError
from app.agents.llm import (
    AnthropicAgentProvider,
    GeminiAgentProvider,
    LLMAgentProvider,
    OpenAIAgentProvider,
    parse_json,
)
from app.pipeline.contracts import EditContext, PlanItem, Segment


class Canned(LLMAgentProvider):
    """Returns queued JSON responses and records what was sent."""

    name = "canned"

    def __init__(self, *responses, models=None):
        super().__init__(models)
        self.responses = list(responses)
        self.calls = []

    def _json(self, task, system, parts, schema, max_tokens):
        self.calls.append((task, system, parts, schema, max_tokens))
        self._record(task, 10, 5)
        r = self.responses.pop(0)
        if isinstance(r, Exception):
            raise r
        return r


def ctx(**kw):
    base = dict(genre="travel", vibe="chill", target_seconds=30, playbook={"hook": "x", "audio": {"a": 1}})
    base.update(kw)
    return EditContext(**base)


def seg(i, kind="video"):
    return Segment(segment_id=f"s{i}", asset_id="a", kind=kind, start=0, end=2)


def item(i, kind="video", desc="beach"):
    return PlanItem(segment_id=f"s{i}", asset_id="a", kind=kind, source_start=0, source_end=3,
                    role="build", description=desc)


# ------------------------------------------------------------------ parse_json
@pytest.mark.parametrize("text,expected", [
    ('{"a": 1}', {"a": 1}),
    ('```json\n{"a": {"b": 2}}\n```', {"a": {"b": 2}}),
    ('Sure! {"x": true} hope that helps', {"x": True}),
])
def test_parse_json_extracts_object(text, expected):
    assert parse_json(text) == expected


@pytest.mark.parametrize("text", ["", None, "no json here", "[1, 2]"])
def test_parse_json_no_object(text):
    with pytest.raises(AgentError, match="did not return JSON"):
        parse_json(text)


def test_parse_json_invalid():
    with pytest.raises(AgentError, match="invalid JSON"):
        parse_json("{not: valid}")


# ------------------------------------------------------------------ model selection / usage
def test_model_for_precedence():
    p = Canned(models={"vision": "v-model", "default": "d-model"})
    assert p.model_for("vision") == "v-model"
    assert p.model_for("script") == "d-model"
    p2 = Canned()
    p2.default_model = "fallback"
    assert p2.model_for("analysis") == "fallback"


def test_record_appends_to_usage_sink():
    p = Canned({"intent": "greeting", "fields": {}, "done": False})
    sink = []
    tok = USAGE_SINK.set(sink)
    try:
        p.analyze_message("hi", AgentContext(state="none"))
    finally:
        USAGE_SINK.reset(tok)
    assert len(sink) == 1 and sink[0].input_tokens == 10 and p.last_usage == sink


def test_record_without_sink():
    p = Canned({"intent": "greeting", "fields": {}, "done": False})
    p.analyze_message("hi", AgentContext(state="none"))
    assert len(p.last_usage) == 1


def test_base_json_not_implemented():
    with pytest.raises(NotImplementedError):
        LLMAgentProvider()._json("t", "s", [], {}, 1)


def test_health_lists_models():
    h = Canned(models={"default": "m"}).health()
    assert h == {"ok": True, "provider": "canned", "models": {"analysis": "m", "vision": "m", "script": "m"}}


# ------------------------------------------------------------------ analyze_message
def test_analyze_message_normalises_output():
    p = Canned({"intent": "create_reel", "confidence": 7, "done": 1,
                "fields": {"genre": "travel", "vibe": None, "title_text": ""},
                "unsupported": list("abcdefg")})
    a = p.analyze_message("make a goa reel", AgentContext(state="none"))
    assert a.intent == "create_reel" and a.confidence == 1.0 and a.done is True
    assert a.fields == {"genre": "travel"}
    assert a.unsupported == list("abcde")


def test_analyze_message_defaults_on_sparse_output():
    a = Canned({}).analyze_message("?", AgentContext(state="none"))
    assert a.intent == "other" and a.confidence == 0.6 and a.fields == {} and a.done is False


def test_analyze_message_negative_confidence_clamped():
    a = Canned({"intent": "x", "confidence": -3, "fields": {}}).analyze_message("?", AgentContext(state="none"))
    assert a.confidence == 0.0


def test_analyze_message_prompt_wraps_and_truncates_user_text():
    p = Canned({"intent": "other", "fields": {}})
    c = AgentContext(state="collecting", pending="genre", prefs={"genre": "travel", "vibe": None},
                     media_summary="2 clips", recent=[("user", "m" * 500)] * 8)
    p.analyze_message("x" * 5000, c)
    task, _, parts, _, _ = p.calls[0]
    prompt = parts[0]
    assert task == "analysis"
    assert "<user_message>\n" + "x" * 2000 + "\n</user_message>" in prompt
    assert "x" * 2001 not in prompt
    assert '"genre": "travel"' in prompt and "vibe" not in prompt
    assert prompt.count("user: ") == 6 and "m" * 301 not in prompt
    assert "We just asked about: genre" in prompt and "Media received: 2 clips" in prompt


def test_analyze_message_empty_context_placeholders():
    p = Canned({"intent": "other", "fields": {}})
    p.analyze_message("hi", AgentContext(state="none"))
    prompt = p.calls[0][2][0]
    assert "nothing" in prompt and "Media received: none" in prompt and "(none)" in prompt


# ------------------------------------------------------------------ score_moments
def test_score_moments_validates_and_clamps():
    p = Canned({"scores": [
        {"segment_id": "s0", "score": 15, "description": "a beach", "tags": ["water"] * 10,
         "reason": "nice", "focus_x": 2},
        {"segment_id": "s1", "score": -1, "description": "x", "reason": "y", "focus_x": None},
        {"segment_id": "unknown", "score": 5, "description": "x", "reason": "y"},
        {"segment_id": "s2", "score": "bad", "description": "x", "reason": "y"},
    ]})
    out = p.score_moments([seg(0), seg(1), seg(2, "photo")],
                          {"s0": [b"j0"], "s1": [b"j1"], "s2": [b"j2"]}, ctx())
    by = {s.segment_id: s for s in out}
    assert set(by) == {"s0", "s1"}
    assert by["s0"].score == 10 and by["s0"].focus_x == 1 and len(by["s0"].tags) == 8
    assert by["s1"].score == 0 and by["s1"].focus_x == 0.5


def test_score_moments_skips_segments_without_frames_and_formats_spans():
    p = Canned({"scores": [{"segment_id": "s0", "score": 5, "description": "d", "reason": "r"}]})
    p.score_moments([seg(0), seg(1, "photo"), seg(2)], {"s0": [b"a"], "s1": [b"b"]},
                    ctx(instructions="start with beach"))
    parts = p.calls[0][2]
    assert "start with beach" in parts[0] and "<playbook>" in parts[0]
    assert "segment_id=s0 (0.0-2.0s)" in parts and "segment_id=s1 (photo)" in parts
    assert not any("s2" in x for x in parts if isinstance(x, str))
    assert ("image/jpeg", b"a") in parts


def test_score_moments_batches_of_20():
    segs = [seg(i) for i in range(45)]
    frames = {s.segment_id: [b"f"] for s in segs}
    resp = lambda lo, hi: {"scores": [{"segment_id": f"s{i}", "score": 5, "description": "d", "reason": "r"}
                                      for i in range(lo, hi)]}
    p = Canned(resp(0, 20), resp(20, 40), resp(40, 45))
    out = p.score_moments(segs, frames, ctx())
    assert len(p.calls) == 3 and len(out) == 45


def test_score_moments_ignores_ids_from_other_batches():
    segs = [seg(i) for i in range(21)]
    frames = {s.segment_id: [b"f"] for s in segs}
    p = Canned({"scores": [{"segment_id": "s20", "score": 5, "description": "d", "reason": "r"}]},
               {"scores": []})
    with pytest.raises(AgentError, match="no scores"):
        p.score_moments(segs, frames, ctx())


def test_score_moments_no_scores_raises():
    with pytest.raises(AgentError):
        Canned({}).score_moments([seg(0)], {"s0": [b"x"]}, ctx())


# ------------------------------------------------------------------ write_script
def test_write_script_respects_budget_and_indexes():
    # 10s reel → budget max(8, int(9*2.3*0.8)=16) = 16 words (+4 slack)
    p = Canned({"title": "Goa days", "lines": [
        {"item_index": 2, "text": "one two three"},
        {"item_index": 0, "text": "w " * 15},
        {"item_index": 1, "text": "too many words now " * 2},
        {"item_index": 9, "text": "out of range"},
        {"item_index": "bad", "text": "x"},
        {"text": "missing index"},
        {"item_index": 1, "text": ""},
    ]})
    out = p.write_script([item(0), item(1), item(2, "photo")], 10, ctx())
    assert [l.item_index for l in out.lines] == [0, 2]
    assert out.title == "Goa days"


def test_write_script_title_precedence():
    lines = {"lines": [{"item_index": 0, "text": "hello there"}]}
    assert Canned({**lines, "title": "Model"}).write_script([item(0)], 30, ctx(title_text="Mine")).title == "Mine"
    assert Canned({**lines}).write_script([item(0)], 30, ctx()).title == "My reel"


def test_write_script_empty_raises():
    with pytest.raises(AgentError, match="empty script"):
        Canned({"title": "t", "lines": []}).write_script([item(0)], 30, ctx())


def test_write_script_prompt_contents():
    p = Canned({"lines": [{"item_index": 0, "text": "hi"}]})
    p.write_script([item(0), item(1, "photo")], 2, ctx(instructions="be funny"))
    prompt = p.calls[0][2][0]
    assert "at most 8 words" in prompt  # minimum budget
    moments = json.loads(prompt.split("Moments in order: ")[1].split("\n")[0])
    assert moments[0]["seconds"] == 3.0 and moments[1]["seconds"] is None
    assert "be funny" in prompt and "suggest one" in prompt


# ------------------------------------------------------------------ Gemini
class FakeGeminiModels:
    def __init__(self, resp=None, exc=None):
        self.resp, self.exc, self.calls = resp, exc, []

    def generate_content(self, **kw):
        self.calls.append(kw)
        if self.exc:
            raise self.exc
        return self.resp

    def get(self, model):
        if self.exc:
            raise self.exc


def gemini(resp=None, exc=None):
    models = FakeGeminiModels(resp, exc)
    return GeminiAgentProvider(client=SimpleNamespace(models=models)), models


def test_gemini_requires_key_without_client():
    with pytest.raises(ValueError, match="GEMINI_API_KEY"):
        GeminiAgentProvider(api_key=None)


def test_gemini_json_success_records_usage():
    resp = SimpleNamespace(text='{"intent": "help", "fields": {}}',
                           usage_metadata=SimpleNamespace(prompt_token_count=12, candidates_token_count=3))
    p, m = gemini(resp)
    a = p.analyze_message("help", AgentContext(state="none"))
    assert a.intent == "help"
    assert p.last_usage[0].input_tokens == 12 and p.last_usage[0].output_tokens == 3
    assert m.calls[0]["model"] == "gemini-flash-latest"
    assert m.calls[0]["config"].response_mime_type == "application/json"


@pytest.mark.parametrize("model", ["google/gemini-3.8-flash", "gemini-3.8-flash"])
def test_gemini_uses_native_model_id_for_api_and_usage(model):
    remote = FakeGeminiModels(SimpleNamespace(text='{"intent": "help", "fields": {}}',
        usage_metadata=SimpleNamespace(prompt_token_count=12, candidates_token_count=3)))
    agent = GeminiAgentProvider(models={"default": model, "vision": model, "script": model},
                                client=SimpleNamespace(models=remote))
    agent.analyze_message("help", AgentContext(state="none"))
    assert remote.calls[0]["model"] == "gemini-3.8-flash"
    assert agent.last_usage[0].model == "gemini-3.8-flash"
    assert all(agent.model_for(task) == "gemini-3.8-flash" for task in ("analysis", "vision", "script"))
    agent.transcribe(b"audio", "audio/ogg")
    assert remote.calls[-1]["model"] == "gemini-3.8-flash"


def test_gateway_model_name_is_unchanged_for_openai_compatible():
    agent = OpenAIAgentProvider(models={"default": "google/gemini-3.8-flash"},
                               client=SimpleNamespace(), name="openai_compatible")
    assert agent.model_for("vision") == "google/gemini-3.8-flash"


def test_gemini_image_parts_converted():
    p, _ = gemini()
    out = p._contents(["text", ("image/jpeg", b"\xff\xd8")])
    assert out[0] == "text" and out[1].inline_data.mime_type == "image/jpeg"


def test_gemini_missing_usage_and_text():
    p, _ = gemini(SimpleNamespace(text=None, usage_metadata=None))
    with pytest.raises(AgentError, match="did not return JSON"):
        p._json("analysis", "s", ["x"], {}, 10)
    assert p.last_usage[0].input_tokens == 0


def test_gemini_request_error_wrapped():
    p, _ = gemini(exc=RuntimeError("boom"))
    with pytest.raises(AgentError, match="gemini request failed: RuntimeError: boom"):
        p._json("analysis", "s", ["x"], {}, 10)


def test_gemini_transcribe():
    p, m = gemini(SimpleNamespace(text="  hello world \n"))
    assert p.transcribe(b"ogg", "audio/ogg") == "hello world"
    assert m.calls[0]["contents"][0].inline_data.mime_type == "audio/ogg"
    p2, _ = gemini(SimpleNamespace(text=None))
    assert p2.transcribe(b"x", "audio/ogg") == ""


def test_gemini_transcribe_error():
    p, _ = gemini(exc=ValueError("x"))
    with pytest.raises(AgentError, match="transcription failed: ValueError"):
        p.transcribe(b"x", "audio/ogg")


def test_gemini_health():
    p, _ = gemini()
    assert p.health()["ok"] is True
    p2, _ = gemini(exc=PermissionError("bad key"))
    h = p2.health()
    assert h["ok"] is False and "PermissionError: bad key" in h["error"]


# ------------------------------------------------------------------ OpenAI-compatible
def openai(handler, **kw):
    sent = []

    def wrap(req):
        sent.append(req)
        return handler(req)

    client = httpx.Client(transport=httpx.MockTransport(wrap))
    return OpenAIAgentProvider(client=client, **kw), sent


def ok_body(content, usage=None):
    return {"choices": [{"message": {"content": content}}], **({"usage": usage} if usage else {})}


def test_openai_success_body_and_usage():
    p, sent = openai(lambda r: httpx.Response(200, json=ok_body('{"scores": []}', {"prompt_tokens": 7,
                                                                                   "completion_tokens": 2})),
                     base_url="https://x.test/v1/", models={"vision": "vm"})
    assert p._json("vision", "SYS", ["hi", ("image/png", b"abc")], {"type": "object"}, 99) == {"scores": []}
    req = sent[0]
    assert str(req.url) == "https://x.test/v1/chat/completions"
    body = json.loads(req.content)
    assert body["model"] == "vm" and body["max_tokens"] == 99
    assert body["response_format"] == {"type": "json_object"}
    assert "JSON Schema" in body["messages"][0]["content"]
    user = body["messages"][1]["content"]
    assert user[0] == {"type": "text", "text": "hi"}
    assert user[1]["image_url"]["url"] == "data:image/png;base64,YWJj"
    assert p.last_usage[0].input_tokens == 7


def test_openai_auth_header_and_name():
    p = OpenAIAgentProvider(api_key="sk-1", name="openrouter")
    assert p.client.headers["authorization"] == "Bearer sk-1" and p.name == "openrouter"
    assert p.base == "https://api.openai.com/v1"
    assert "authorization" not in OpenAIAgentProvider().client.headers


def test_openai_http_error_status():
    p, _ = openai(lambda r: httpx.Response(429, text="slow down"))
    with pytest.raises(AgentError, match="openai error 429: slow down"):
        p._json("analysis", "s", ["x"], {}, 1)


def test_openai_transport_error():
    def boom(r):
        raise httpx.ConnectError("nope")

    p, _ = openai(boom)
    with pytest.raises(AgentError, match="request failed: ConnectError"):
        p._json("analysis", "s", ["x"], {}, 1)


@pytest.mark.parametrize("resp", [
    httpx.Response(200, text="not json"),
    httpx.Response(200, json={"choices": []}),
    httpx.Response(200, json={"nope": 1}),
])
def test_openai_unexpected_response(resp):
    p, _ = openai(lambda r: resp)
    with pytest.raises(AgentError, match="unexpected response"):
        p._json("analysis", "s", ["x"], {}, 1)


def test_openai_null_content():
    p, _ = openai(lambda r: httpx.Response(200, json=ok_body(None)))
    with pytest.raises(AgentError, match="did not return JSON"):
        p._json("analysis", "s", ["x"], {}, 1)


def test_openai_health():
    p, sent = openai(lambda r: httpx.Response(200, json={}))
    assert p.health()["ok"] is True and sent[0].url.path.endswith("/models")
    p2, _ = openai(lambda r: httpx.Response(401))
    h2 = p2.health()
    assert h2["ok"] is False and h2["error"] == "HTTP 401"

    def boom(r):
        raise httpx.ReadTimeout("t")

    p3, _ = openai(boom)
    h3 = p3.health()
    assert h3["ok"] is False and h3["error"] == "ReadTimeout"


# ------------------------------------------------------------------ Anthropic
class FakeMessages:
    def __init__(self, resp):
        self.resp, self.calls = resp, []

    def create(self, **kw):
        self.calls.append(kw)
        return self.resp


def anthropic_provider(resp):
    msgs = FakeMessages(resp)
    return AnthropicAgentProvider(client=SimpleNamespace(beta=SimpleNamespace(messages=msgs))), msgs


def test_anthropic_success():
    resp = SimpleNamespace(stop_reason="end_turn", usage=SimpleNamespace(input_tokens=4, output_tokens=1),
                           content=[SimpleNamespace(type="thinking"), SimpleNamespace(type="text", text='{"a":1}')])
    p, m = anthropic_provider(resp)
    assert p._json("script", "SYS", ["t", ("image/jpeg", b"abc")], {}, 50) == {"a": 1}
    kw = m.calls[0]
    assert kw["model"] == "claude-opus-5-5" and kw["max_tokens"] == 50
    content = kw["messages"][0]["content"]
    assert content[1]["source"] == {"type": "base64", "media_type": "image/jpeg", "data": "YWJj"}
    assert p.last_usage[0].output_tokens == 1


def test_anthropic_refusal():
    p, _ = anthropic_provider(SimpleNamespace(stop_reason="refusal", content=[]))
    with pytest.raises(AgentError, match="declined"):
        p._json("analysis", "s", ["x"], {}, 1)


def test_anthropic_no_text_block():
    p, _ = anthropic_provider(SimpleNamespace(stop_reason="end_turn", usage=None, content=[]))
    with pytest.raises(AgentError, match="did not return JSON"):
        p._json("analysis", "s", ["x"], {}, 1)
