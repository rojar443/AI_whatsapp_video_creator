"""Pipeline step registry: spec resolution, Python plugins and the external HTTP agent protocol."""
import json

import httpx
import pytest

from app import providers
from app.config import get_settings
from app.pipeline import registry
from app.pipeline.builtin.narration import Narrator
from app.pipeline.contracts import CaptionsOutput, MusicInput, MusicOutput, NarrationInput
from app.pipeline.registry import HttpStep, PluginStep, StepError
from app.tts import TTSError


class EchoMusic:
    """Plugin used via python:tests.test_pipeline_registry:EchoMusic."""

    def run(self, inp):
        return {"audio_path": None, "title": f"{inp.energy} track", "license": "CC0"}


class ModelMusic:
    def run(self, inp):
        return MusicOutput(audio_path=None, title="model")


class ForeignModelMusic:
    def run(self, inp):
        return CaptionsOutput(subtitles_path=None)  # wrong model type, compatible-ish fields


MUSIC_IN = MusicInput(moods=["calm"], energy="low", seconds=5, work_dir="/tmp")


# ------------------------------------------------------------------ spec resolution
@pytest.mark.parametrize("path", ["nocolon", ":Cls", "mod:"])
def test_load_class_invalid(path):
    with pytest.raises(ValueError, match="invalid plugin path"):
        registry._load_class(path)


def test_get_step_invalid_spec():
    with pytest.raises(ValueError, match="invalid pipeline spec"):
        registry.get_step("music", "ftp://nope")


def test_get_step_kinds():
    assert registry.get_step("music", "builtin").impl == "builtin"
    assert isinstance(registry.get_step("music", "python:tests.test_pipeline_registry:EchoMusic"), PluginStep)
    assert isinstance(registry.get_step("music", "https://agent.test/music"), HttpStep)


def test_configured_specs_from_file_and_env(tmp_path, monkeypatch):
    cfg = tmp_path / "pipeline.yaml"
    cfg.write_text("steps:\n  music: https://file.test/music\n  render: 5\n  unknown: x\n")
    s = get_settings()
    monkeypatch.setattr(s, "pipeline_config_file", str(cfg))
    monkeypatch.setattr(s, "pipeline_captions", " python:x:Y ")
    specs = registry.configured_specs()
    assert specs["music"] == "https://file.test/music" and specs["render"] == "builtin"
    assert specs["captions"] == "python:x:Y" and "unknown" not in specs


def test_configured_specs_empty_file(tmp_path, monkeypatch):
    cfg = tmp_path / "p.yaml"
    cfg.write_text("")
    monkeypatch.setattr(get_settings(), "pipeline_config_file", str(cfg))
    assert set(registry.configured_specs().values()) == {"builtin"}


# ------------------------------------------------------------------ plugins
@pytest.mark.parametrize("cls,title", [("EchoMusic", "low track"), ("ModelMusic", "model")])
def test_plugin_step_validates_output(cls, title):
    step = PluginStep("music", f"python:tests.test_pipeline_registry:{cls}")
    out = step.run(MUSIC_IN)
    assert isinstance(out, MusicOutput) and out.title == title


def test_plugin_step_invalid_output_raises_validation_error():
    # Note: not wrapped in StepError; render_job treats it as a crash (final-failure hook refunds).
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        PluginStep("music", "python:tests.test_pipeline_registry:ForeignModelMusic").run(MUSIC_IN)


class Boom:
    def run(self, inp):
        raise RuntimeError("plugin crashed")


def test_run_step_falls_back_to_builtin(monkeypatch, tmp_path):
    s = get_settings()
    monkeypatch.setattr(s, "pipeline_music", "python:tests.test_pipeline_registry:Boom")
    monkeypatch.setattr(s, "pipeline_fallback_to_builtin", True)
    monkeypatch.setattr("app.pipeline.builtin.music.ffmpeg.run", lambda args: None)
    monkeypatch.setattr(s, "music_library_dir", str(tmp_path))
    used = {}
    out = registry.run_step("music", MusicInput(moods=[], seconds=3, work_dir=str(tmp_path)), used)
    assert out.generated and used["music"] == "builtin (fallback from python:tests.test_pipeline_registry:Boom)"


def test_run_step_no_fallback_raises(monkeypatch):
    s = get_settings()
    monkeypatch.setattr(s, "pipeline_music", "python:tests.test_pipeline_registry:Boom")
    monkeypatch.setattr(s, "pipeline_fallback_to_builtin", False)
    with pytest.raises(RuntimeError, match="plugin crashed"):
        registry.run_step("music", MUSIC_IN)


def test_run_step_records_impl():
    registry._test_overrides.pop("music", None)
    used = {}
    step = registry.get_step("music", "python:tests.test_pipeline_registry:EchoMusic")
    registry._test_overrides["music"] = step
    registry.run_step("music", MUSIC_IN, used)
    assert used == {"music": "python:tests.test_pipeline_registry:EchoMusic"}


def test_describe_lists_all_steps():
    d = registry.describe()
    assert [x["step"] for x in d] == list(registry.STEPS)


# ------------------------------------------------------------------ HTTP agent
def http_step(handler, step="music"):
    sent = []

    def wrap(req):
        sent.append(req)
        return handler(req)

    return HttpStep(step, "https://agent.test/run", client=httpx.Client(transport=httpx.MockTransport(wrap))), sent


def test_http_step_auth_header(monkeypatch):
    monkeypatch.setattr(get_settings(), "pipeline_http_token", "tok")
    s = HttpStep("music", "https://a.test")
    assert s.client.headers["authorization"] == "Bearer tok"
    assert s.client.headers["user-agent"] == "reelchat-pipeline/1"


def test_http_step_payload_signs_paths_and_downloads_outputs(tmp_path):
    def handler(req):
        if req.url.path == "/out.wav":
            return httpx.Response(200, content=b"WAV")
        body = json.loads(req.content)
        assert body["step"] == "music" and "work_dir" not in body["input"]
        return httpx.Response(200, json={"audio_url": "https://agent.test/out.wav", "title": "t",
                                         "nested": [{"x_url": "not-a-url"}]})

    step, sent = http_step(handler)
    out = step.run(MusicInput(moods=[], seconds=3, work_dir=str(tmp_path)))
    assert out.audio_path.endswith(".wav") and open(out.audio_path, "rb").read() == b"WAV"


def test_paths_to_urls():
    out = registry._paths_to_urls({"frame_paths": ["/a.jpg", 3], "audio_path": "/b.wav", "work_dir": "/w",
                                   "items": [{"file_path": "/c"}], "n": 1}, "run")
    assert len(out["frame_urls"]) == 1 and out["audio_url"].startswith("http")
    assert "work_dir" not in out and out["items"][0]["file_url"].startswith("http") and out["n"] == 1


@pytest.mark.parametrize("resp,retryable,code", [
    (httpx.Response(503), True, "agent_error"), (httpx.Response(429), True, "agent_error"),
    (httpx.Response(422, text="bad input"), False, "step_failed"),
])
def test_http_step_status_errors(tmp_path, resp, retryable, code):
    step, _ = http_step(lambda r: resp)
    with pytest.raises(StepError) as ei:
        step.run(MUSIC_IN)
    assert ei.value.retryable is retryable and ei.value.code == code


def test_http_step_unreachable():
    def boom(r):
        raise httpx.ConnectError("refused")

    step, _ = http_step(boom)
    with pytest.raises(StepError) as ei:
        step.run(MUSIC_IN)
    assert ei.value.retryable and ei.value.code == "agent_unreachable"


@pytest.mark.parametrize("resp", [httpx.Response(200, text="nope"), httpx.Response(200, json={"wrong": 1}),
                                  httpx.Response(200, json=["list"])])
def test_http_step_invalid_response(resp):
    step, _ = http_step(lambda r: resp)
    with pytest.raises(StepError, match="invalid response"):
        step.run(MUSIC_IN)


def test_http_step_default_work_dir(tmp_path, monkeypatch):
    from app.pipeline.contracts import ScoringInput, EditContext

    monkeypatch.setattr(get_settings(), "work_dir", str(tmp_path))
    step, _ = http_step(lambda r: httpx.Response(200, json={"scores": [], "scorer": "x"}), step="moment_scoring")
    out = step.run(ScoringInput(segments=[], context=EditContext(genre="travel", playbook={})))
    assert out.scorer == "x"


def test_download_size_limit(tmp_path):
    client = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(200, content=b"x" * 20)))
    with pytest.raises(StepError, match="too large"):
        registry._download("https://a.test/f.mp4", tmp_path, client, limit=10)
    assert registry._download("https://a.test/f.mp4", tmp_path, client, limit=100).endswith(".mp4")


def test_download_http_error(tmp_path):
    client = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(404)))
    with pytest.raises(httpx.HTTPStatusError):
        registry._download("https://a.test/f", tmp_path, client)


# ------------------------------------------------------------------ narration step
class FakeTTS:
    is_demo = False

    def __init__(self, name="elevenlabs", exc=None):
        self.name, self.exc = name, exc

    def synthesize(self, text, out, vibe=None):
        from app.tts import Speech

        if self.exc:
            raise self.exc
        open(out, "wb").write(b"x")
        return Speech(out, [("hi", 0.0, 0.4)])


@pytest.mark.parametrize("retryable", [True, False])
def test_narrator_tts_error(tmp_path, retryable):
    providers._overrides["tts"] = FakeTTS(exc=TTSError("quota", retryable=retryable))
    with pytest.raises(StepError) as ei:
        Narrator().run(NarrationInput(text="hi", work_dir=str(tmp_path)))
    assert ei.value.retryable is retryable and ei.value.code == "narration_failed"


def test_narrator_success(tmp_path, monkeypatch):
    from app.pipeline.builtin import narration

    monkeypatch.setattr(narration.ffmpeg, "duration", lambda p: 1.5)
    providers._overrides["tts"] = FakeTTS()
    out = Narrator().run(NarrationInput(text="hi", work_dir=str(tmp_path)))
    assert out.audio_path.endswith("narration.mp3") and out.duration == 1.5 and out.words[0].word == "hi"
    providers._overrides["tts"] = FakeTTS(name="espeak")
    assert Narrator().run(NarrationInput(text="hi", work_dir=str(tmp_path))).audio_path.endswith(".wav")
