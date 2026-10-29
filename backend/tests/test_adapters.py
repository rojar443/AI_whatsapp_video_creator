"""External adapters with mocked transports: Meta Cloud API, SMTP email, TTS vendors, transcription."""
import base64
import json
import subprocess
from types import SimpleNamespace

import httpx
import pytest

from app import tts
from app.agents.base import AgentError
from app.config import get_settings
from app.messaging.base import SendError
from app.messaging.meta_cloud import MetaCloudProvider
from app.services import email
from app.transcription import (
    GeminiTranscriptionProvider,
    MockTranscriptionProvider,
    TranscriptionError,
    base_mime,
    looks_like_audio,
)


# ------------------------------------------------------------------ Meta Cloud API
def meta(handler):
    sent = []

    def wrap(req):
        sent.append(req)
        return handler(req)

    return MetaCloudProvider("tok", "PNID", "v99.0", client=httpx.Client(transport=httpx.MockTransport(wrap))), sent


def ok_msg(req):
    return httpx.Response(200, json={"messages": [{"id": "wamid.1"}]})


def body(req):
    return json.loads(req.content)


@pytest.mark.parametrize("tok,pid", [("", "1"), ("t", ""), (None, None)])
def test_meta_requires_credentials(tok, pid):
    with pytest.raises(ValueError, match="WHATSAPP_ACCESS_TOKEN"):
        MetaCloudProvider(tok, pid)


def test_meta_default_client_sets_bearer():
    p = MetaCloudProvider("secret", "1")
    assert p.client.headers["authorization"] == "Bearer secret" and p.base.endswith("/v21.0")


def test_meta_send_text_truncates():
    p, sent = meta(ok_msg)
    assert p.send_text("123", "x" * 5000) == "wamid.1"
    b = body(sent[0])
    assert str(sent[0].url) == "https://graph.facebook.com/v99.0/PNID/messages"
    assert b["messaging_product"] == "whatsapp" and b["recipient_type"] == "individual"
    assert len(b["text"]["body"]) == 4096 and b["text"]["preview_url"] is True


def test_meta_send_buttons_limits():
    p, sent = meta(ok_msg)
    btns = [(f"id{i}" + "x" * 300, "A very long button title") for i in range(5)]
    p.send_buttons("1", "Pick one", btns)
    action = body(sent[0])["interactive"]["action"]["buttons"]
    assert len(action) == 3
    assert len(action[0]["reply"]["id"]) == 256 and len(action[0]["reply"]["title"]) == 20


def test_meta_send_buttons_long_body_splits():
    p, sent = meta(ok_msg)
    p.send_buttons("1", "y" * 1500, [("ok", "OK")])
    assert len(sent) == 2
    assert body(sent[0])["type"] == "text"
    assert body(sent[1])["interactive"]["body"]["text"] == "Shall I proceed?"


@pytest.mark.parametrize("status,retryable", [(429, True), (500, True), (503, True), (400, False), (404, False)])
def test_meta_error_retryability(status, retryable):
    p, _ = meta(lambda r: httpx.Response(status, json={"error": {"code": 131047, "message": "window closed"}}))
    with pytest.raises(SendError) as ei:
        p.send_text("1", "hi")
    assert ei.value.retryable is retryable and ei.value.code == "131047"
    assert "window closed" in str(ei.value)


def test_meta_error_non_json_body():
    p, _ = meta(lambda r: httpx.Response(502, text="Bad gateway", headers={"content-type": "text/html"}))
    with pytest.raises(SendError) as ei:
        p.send_text("1", "hi")
    assert ei.value.code == "502" and "Bad gateway" in str(ei.value)


def test_meta_network_error_retryable():
    def boom(r):
        raise httpx.ConnectTimeout("t")

    p, _ = meta(boom)
    with pytest.raises(SendError) as ei:
        p.send_text("1", "hi")
    assert ei.value.retryable is True


def test_meta_send_template():
    p, sent = meta(ok_msg)
    p.send_template("1", "reel_ready", "en_US", ["Abhi", "z" * 2000])
    t = body(sent[0])["template"]
    assert t["name"] == "reel_ready" and t["language"] == {"code": "en_US"}
    params = t["components"][0]["parameters"]
    assert params[0]["text"] == "Abhi" and len(params[1]["text"]) == 1000
    p.send_template("1", "hello", "en", [])
    assert body(sent[1])["template"]["components"] == []


def test_meta_send_video_uploads_then_sends(tmp_path):
    f = tmp_path / "reel.mp4"
    f.write_bytes(b"\x00" * 10)

    def handler(req):
        if req.url.path.endswith("/media"):
            assert b"messaging_product" in req.content and b"video/mp4" in req.content
            return httpx.Response(200, json={"id": "MEDIA1"})
        return ok_msg(req)

    p, sent = meta(handler)
    assert p.send_video("1", str(f), "c" * 2000) == "wamid.1"
    v = body(sent[1])["video"]
    assert v["id"] == "MEDIA1" and len(v["caption"]) == 1024


@pytest.mark.parametrize("status,retryable", [(500, True), (400, False)])
def test_meta_upload_errors(tmp_path, status, retryable):
    f = tmp_path / "x.unknownext"
    f.write_bytes(b"1")
    p, _ = meta(lambda r: httpx.Response(status, text="nope"))
    with pytest.raises(SendError, match="upload failed") as ei:
        p.upload_media(str(f))
    assert ei.value.retryable is retryable


def test_meta_upload_network_error(tmp_path):
    f = tmp_path / "x.mp4"
    f.write_bytes(b"1")

    def boom(r):
        raise httpx.ReadError("r")

    p, _ = meta(boom)
    with pytest.raises(SendError, match="upload failed"):
        p.upload_media(str(f))


def media_handler(info, payload=b"abc", cdn_status=200):
    def h(req):
        if req.url.host == "cdn.test":
            return httpx.Response(cdn_status, content=payload)
        return httpx.Response(200, json=info)
    return h


def test_meta_download_media(tmp_path):
    dest = tmp_path / "m"
    p, _ = meta(media_handler({"url": "https://cdn.test/f", "mime_type": "video/mp4", "file_size": 3}))
    assert p.download_media("MID", str(dest), 10) == "video/mp4"
    assert dest.read_bytes() == b"abc"


def test_meta_download_declared_too_large(tmp_path):
    p, _ = meta(media_handler({"url": "https://cdn.test/f", "file_size": 999}))
    with pytest.raises(SendError) as ei:
        p.download_media("MID", str(tmp_path / "m"), 10)
    assert ei.value.code == "too_large" and not ei.value.retryable


def test_meta_download_streamed_too_large(tmp_path):
    p, _ = meta(media_handler({"url": "https://cdn.test/f"}, payload=b"x" * 50))
    with pytest.raises(SendError) as ei:
        p.download_media("MID", str(tmp_path / "m"), 10)
    assert ei.value.code == "too_large"


@pytest.mark.parametrize("status,retryable", [(404, False), (429, True), (500, True)])
def test_meta_download_http_status(tmp_path, status, retryable):
    p, _ = meta(media_handler({"url": "https://cdn.test/f"}, cdn_status=status))
    with pytest.raises(SendError) as ei:
        p.download_media("MID", str(tmp_path / "m"), 10)
    assert ei.value.retryable is retryable and str(status) in str(ei.value)


def test_meta_download_missing_url(tmp_path):
    p, _ = meta(lambda r: httpx.Response(200, json={}))
    with pytest.raises(SendError, match="media download failed"):
        p.download_media("MID", str(tmp_path / "m"), 10)


def test_meta_download_no_mime(tmp_path):
    p, _ = meta(media_handler({"url": "https://cdn.test/f"}))
    assert p.download_media("MID", str(tmp_path / "m"), 10) == ""


def test_meta_health():
    p, sent = meta(lambda r: httpx.Response(200, json={}))
    assert p.health() == {"ok": True, "provider": "meta", "status_code": 200}
    assert sent[0].url.params["fields"] == "display_phone_number"
    p2, _ = meta(lambda r: httpx.Response(401))
    assert p2.health()["ok"] is False

    def boom(r):
        raise httpx.ConnectError("down")

    p3, _ = meta(boom)
    assert p3.health() == {"ok": False, "provider": "meta", "error": "down"}


# ------------------------------------------------------------------ email
def test_console_email_keeps_last_20(monkeypatch):
    monkeypatch.setattr(get_settings(), "email_provider", "console")
    email.DEV_OUTBOX.clear()
    for i in range(25):
        email.send_email("a@b.c", f"s{i}", "body")
    assert len(email.DEV_OUTBOX) == 20 and email.DEV_OUTBOX[0]["subject"] == "s5"


class FakeSMTP:
    instances = []

    def __init__(self, host, port, timeout):
        self.host, self.port, self.events = host, port, []
        FakeSMTP.instances.append(self)

    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.events.append("quit")

    def starttls(self):
        self.events.append("tls")

    def login(self, u, p):
        self.events.append(("login", u, p))

    def send_message(self, msg):
        self.events.append(("send", msg["To"], msg["Subject"], msg["From"]))


@pytest.mark.parametrize("user,expected_login", [("me", ("login", "me", "")), (None, None)])
def test_smtp_email(monkeypatch, user, expected_login):
    s = get_settings()
    for k, v in dict(email_provider="smtp", smtp_host=None, smtp_port=2525, smtp_from="noreply@x",
                     smtp_username=user, smtp_password=None).items():
        monkeypatch.setattr(s, k, v)
    FakeSMTP.instances.clear()
    monkeypatch.setattr(email.smtplib, "SMTP", FakeSMTP)
    email.send_email("u@x", "Verify", "click")
    smtp = FakeSMTP.instances[0]
    assert (smtp.host, smtp.port) == ("localhost", 2525)
    assert smtp.events[0] == "tls" and ("send", "u@x", "Verify", "noreply@x") in smtp.events
    assert (expected_login in smtp.events) if expected_login else not any(
        isinstance(e, tuple) and e[0] == "login" for e in smtp.events)


# ------------------------------------------------------------------ TTS
def test_words_from_alignment():
    a = {"characters": list(" hi  yo "), "character_start_times_seconds": [0, .1, .2, .3, .4, .5, .6, .7],
         "character_end_times_seconds": [.1, .2, .3, .4, .5, .6, .7, .8]}
    assert tts.words_from_alignment(a) == [("hi", .1, .3), ("yo", .5, .7)]
    assert tts.words_from_alignment({}) == []
    assert tts.words_from_alignment({"characters": ["a"], "character_start_times_seconds": [0],
                                     "character_end_times_seconds": [1]}) == [("a", 0.0, 1.0)]


def eleven(handler):
    sent = []

    def wrap(req):
        sent.append(req)
        return handler(req)

    return tts.ElevenLabsTTS("k", "VOICE", "model-x", "https://el.test/",
                             client=httpx.Client(transport=httpx.MockTransport(wrap))), sent


def test_elevenlabs_requires_key():
    with pytest.raises(ValueError, match="ELEVENLABS_API_KEY"):
        tts.ElevenLabsTTS("", "v", "m")
    assert tts.ElevenLabsTTS("k", "v", "m").client.headers["xi-api-key"] == "k"


@pytest.mark.parametrize("vibe,stability", [("calm", 0.65), ("cinematic", 0.65), ("upbeat", 0.45), (None, 0.45)])
def test_elevenlabs_synthesize(tmp_path, vibe, stability):
    payload = {"audio_base64": base64.b64encode(b"MP3").decode(),
               "alignment": {"characters": ["o", "k"], "character_start_times_seconds": [0, .1],
                             "character_end_times_seconds": [.1, .2]}}
    p, sent = eleven(lambda r: httpx.Response(200, json=payload))
    out = tmp_path / "a.mp3"
    sp = p.synthesize("ok", str(out), vibe=vibe)
    assert out.read_bytes() == b"MP3" and sp.words == [("ok", 0.0, .2)]
    req = sent[0]
    assert req.url.path == "/v1/text-to-speech/VOICE/with-timestamps"
    assert req.url.params["output_format"] == "mp3_44100_128"
    b = json.loads(req.content)
    assert b["model_id"] == "model-x" and b["voice_settings"]["stability"] == stability


def test_elevenlabs_voice_override_and_no_alignment(tmp_path):
    p, sent = eleven(lambda r: httpx.Response(200, json={"audio_base64": ""}))
    assert p.synthesize("x", str(tmp_path / "a"), voice_id="OTHER").words == []
    assert "/OTHER/" in sent[0].url.path


@pytest.mark.parametrize("status,retryable", [(429, True), (500, True), (401, False), (422, False)])
def test_elevenlabs_errors(tmp_path, status, retryable):
    p, _ = eleven(lambda r: httpx.Response(status, text="bad"))
    with pytest.raises(tts.TTSError) as ei:
        p.synthesize("x", str(tmp_path / "a"))
    assert ei.value.retryable is retryable


def test_elevenlabs_network_error(tmp_path):
    def boom(r):
        raise httpx.ConnectError("x")

    p, _ = eleven(boom)
    with pytest.raises(tts.TTSError) as ei:
        p.synthesize("x", str(tmp_path / "a"))
    assert ei.value.retryable


def test_elevenlabs_voices():
    p, _ = eleven(lambda r: httpx.Response(200, json={"voices": [{"voice_id": "a", "name": "Ann"}, {"voice_id": "b"}]}))
    assert p.voices() == [{"voice_id": "a", "name": "Ann"}, {"voice_id": "b", "name": None}]
    p2, _ = eleven(lambda r: httpx.Response(500))
    with pytest.raises(httpx.HTTPStatusError):
        p2.voices()


def test_espeak_missing(monkeypatch):
    monkeypatch.setattr(tts.shutil, "which", lambda b: None)
    with pytest.raises(ValueError, match="espeak-ng"):
        tts.EspeakTTS()


def test_espeak_synthesize(monkeypatch, tmp_path):
    monkeypatch.setattr(tts.shutil, "which", lambda b: "/usr/bin/espeak-ng" if b == "espeak-ng" else None)
    calls = []

    def run(cmd, **kw):
        calls.append(cmd)
        return SimpleNamespace(returncode=0 if "good" in cmd else 1, stderr=b"voice missing")

    monkeypatch.setattr(subprocess, "run", run)
    e = tts.EspeakTTS()
    assert e.synthesize("good", str(tmp_path / "a.wav")).path.endswith("a.wav")
    assert calls[0][:2] == ["/usr/bin/espeak-ng", "-v"] and calls[0][-2:] == ["--", "good"]
    with pytest.raises(tts.TTSError, match="voice missing"):
        e.synthesize("-rf bad", str(tmp_path / "b.wav"))


def test_get_tts_factory(monkeypatch):
    s = get_settings()
    monkeypatch.setattr(s, "elevenlabs_api_key", "k")
    assert isinstance(tts.get_tts("elevenlabs"), tts.ElevenLabsTTS)
    assert isinstance(tts.get_tts("anything"), tts.MockTTS)
    monkeypatch.setattr(tts.shutil, "which", lambda b: "/bin/espeak")
    assert isinstance(tts.get_tts("espeak"), tts.EspeakTTS)


def test_mock_tts_duration(monkeypatch, tmp_path):
    from app.media import ffmpeg

    seen = []
    monkeypatch.setattr(ffmpeg, "run", lambda args: seen.append(args))
    tts.MockTTS().synthesize("one two three four five six seven eight nine ten", str(tmp_path / "s.mp3"))
    tts.MockTTS().synthesize("hi", str(tmp_path / "t.mp3"))
    assert seen[0][seen[0].index("-t") + 1] == "4.00" and seen[1][seen[1].index("-t") + 1] == "1.00"


# ------------------------------------------------------------------ transcription
@pytest.mark.parametrize("mime,expected", [("audio/ogg; codecs=opus", "audio/ogg"), (None, ""), (" AUDIO/MP4 ", "audio/mp4")])
def test_base_mime(mime, expected):
    assert base_mime(mime) == expected


@pytest.mark.parametrize("data,mime,ok", [
    (b"OggS....", "audio/ogg", True),
    (b"ID3\x03", "audio/mpeg", True),
    (b"\x00\x00\x00\x18ftypM4A ", "audio/mp4", True),
    (b"#!AMR\n", "audio/amr", True),
    (b"RIFF....WAVE", "audio/wav", True),
    (b"OggS", "video/mp4", False),
    (b"<html>", "audio/ogg", False),
    (b"", "audio/ogg", False),
])
def test_looks_like_audio(data, mime, ok):
    assert looks_like_audio(data, mime) is ok


def test_mock_transcription():
    m = MockTranscriptionProvider()
    assert m.transcribe(m.embed(b"OggS", "  make a reel "), "audio/ogg").text == "make a reel"
    with pytest.raises(TranscriptionError, match="no speech"):
        m.transcribe(b"OggS", "audio/ogg")
    with pytest.raises(TranscriptionError, match="unreadable"):
        m.transcribe(b"OggS" + m.MARKER + b"{bad", "audio/ogg")
    with pytest.raises(TranscriptionError, match="empty"):
        m.transcribe(m.embed(b"OggS", "   "), "audio/ogg")


class FakeAgent:
    def __init__(self, result):
        self.result, self.calls = result, []

    def transcribe(self, audio, mime):
        self.calls.append(mime)
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


def test_gemini_transcription():
    a = FakeAgent("hello")
    g = GeminiTranscriptionProvider(agent=a)
    assert g.transcribe(b"x", "audio/ogg; codecs=opus").text == "hello" and a.calls == ["audio/ogg"]
    g.transcribe(b"x", "")
    assert a.calls[-1] == "audio/ogg"
    with pytest.raises(TranscriptionError, match="no speech"):
        GeminiTranscriptionProvider(agent=FakeAgent("")).transcribe(b"x", "audio/ogg")
    with pytest.raises(TranscriptionError, match="quota"):
        GeminiTranscriptionProvider(agent=FakeAgent(AgentError("quota"))).transcribe(b"x", "audio/ogg")
