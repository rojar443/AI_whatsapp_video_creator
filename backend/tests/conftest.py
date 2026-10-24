import json
import os
import re
import shutil
import tempfile
import uuid
from pathlib import Path

import pytest

_TMP = Path(tempfile.mkdtemp(prefix="reelchat-test-"))
os.environ.update({
    "APP_ENV": "test",
    "SECRET_KEY": "test-secret-key-0123456789",
    "DATABASE_URL": os.environ.get("TEST_DATABASE_URL", f"sqlite:///{_TMP}/test.db"),
    "STORAGE_LOCAL_DIR": str(_TMP / "storage"),
    "WORK_DIR": str(_TMP / "work"),
    "MESSAGING_PROVIDER": "dev",
    "AGENT_PROVIDER": "rules",
    "VIDEO_PROVIDER": "mock",
    "TTS_PROVIDER": "mock",
    "TRANSCRIPTION_PROVIDER": "mock",
    "WHATSAPP_APP_SECRET": "test-app-secret",
    "WHATSAPP_VERIFY_TOKEN": "test-verify-token",
    "JOB_RETRY_BASE_SECONDS": "0",
    "DEFAULT_CREDITS": "10",
    "PUBLIC_BASE_URL": "http://testserver",
})

from fastapi.testclient import TestClient  # noqa: E402

from app import providers  # noqa: E402
from app.api.deps import limiter  # noqa: E402
from app.db import Base, get_engine, get_sessionmaker  # noqa: E402
from app.jobs.tasks import run_pending  # noqa: E402
from app.main import app  # noqa: E402
from app.messaging.base import build_inbound_payload  # noqa: E402
from app.messaging.dev import DevMessagingProvider  # noqa: E402
from app.models import Conversation, Message, WhatsAppAccount  # noqa: E402
from app.security import meta_signature  # noqa: E402
from app.video import ffmpeg  # noqa: E402

HAS_FFMPEG = ffmpeg.available()
requires_ffmpeg = pytest.mark.skipif(not HAS_FFMPEG, reason="ffmpeg/ffprobe not installed")
FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture(autouse=True)
def _fresh_db():
    engine = get_engine()
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    limiter.reset()
    providers._overrides.clear()
    providers.set_messaging(None)
    DevMessagingProvider.fail_next = 0
    yield
    shutil.rmtree(_TMP / "storage", ignore_errors=True)


@pytest.fixture
def db():
    s = get_sessionmaker()()
    yield s
    s.close()


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


def fresh_client() -> TestClient:
    return TestClient(app)


# --------------------------------------------------------------------------- helpers
def register(client: TestClient, email="asha@example.com", phone="+91 98765 43210", name="Asha Nair",
             password="correct horse battery", verify=True) -> dict:
    r = client.post("/api/auth/register", json={"name": name, "email": email, "password": password,
                                                  "phone": phone, "accept_terms": True})
    assert r.status_code == 201, r.text
    data = r.json()
    client.headers["x-csrf-token"] = data["csrf_token"]
    if verify:
        token = re.search(r"token=([^&\s]+)", data["dev_verification_url"]).group(1)
        assert client.post("/api/auth/verify-email", json={"token": token}).status_code == 200
    return data


def post_webhook(client: TestClient, payload: dict, secret="test-app-secret"):
    raw = json.dumps(payload).encode()
    return client.post("/api/webhooks/whatsapp", content=raw,
                       headers={"content-type": "application/json",
                                "x-hub-signature-256": meta_signature(raw, secret)})


def wa_text(client: TestClient, wa_id: str, text: str, msg_id: str | None = None):
    return post_webhook(client, build_inbound_payload(wa_id, msg_id or f"wamid.{uuid.uuid4().hex}", text=text))


def wa_button(client: TestClient, wa_id: str, button_id: str, title="ok"):
    return post_webhook(client, build_inbound_payload(wa_id, f"wamid.{uuid.uuid4().hex}", button_id=button_id,
                                                      button_title=title))


def wa_voice(client: TestClient, wa_id: str, media_id: str):
    return post_webhook(client, build_inbound_payload(wa_id, f"wamid.{uuid.uuid4().hex}", audio_media_id=media_id))


def outbound(db, wa_id: str) -> list[Message]:
    db.expire_all()
    wa = db.query(WhatsAppAccount).filter_by(wa_id=wa_id).one()
    conv = db.query(Conversation).filter_by(whatsapp_account_id=wa.id).one()
    return (db.query(Message).filter_by(conversation_id=conv.id, direction="outbound")
            .order_by(Message.created_at).all())


def last_reply(db, wa_id: str) -> Message:
    return outbound(db, wa_id)[-1]


def link_whatsapp(client: TestClient, db, wa_id="919876543210") -> str:
    r = client.post("/api/me/whatsapp/link")
    assert r.status_code == 200, r.text
    wa_text(client, wa_id, f"LINK {r.json()['code']}")
    run_pending(db)
    assert "now linked" in last_reply(db, wa_id).body
    return wa_id


def say(client, db, wa_id: str, text: str) -> list[str]:
    """Send a message, drain the queue, return the new outbound bodies."""
    before = len(outbound(db, wa_id))
    wa_text(client, wa_id, text)
    run_pending(db)
    return [m.body for m in outbound(db, wa_id)[before:]]


def confirm_button(db, wa_id: str) -> str:
    for m in reversed(outbound(db, wa_id)):
        for b in (m.payload or {}).get("buttons", []):
            if b["id"].startswith("confirm:"):
                return b["id"]
    raise AssertionError("no confirm button found")


class FakeVideoProvider:
    """Instant provider for non-ffmpeg tests: writes a small fake MP4."""

    name = "fake"
    is_mock = True
    cost_per_second_usd = 0.0
    fail_with = None  # set to a GenerationError to simulate failures
    calls = 0

    class _TTS:
        name = "mock"
        languages = ["english", "hindi", "malayalam", "tamil", "spanish", "french"]

    tts = _TTS()

    def capabilities(self):
        return {"qualities": ["standard", "high"], "languages": self.tts.languages,
                "styles": ["cinematic", "realistic", "animation", "documentary", "minimalist"],
                "music": ["none", "upbeat", "calm", "dramatic", "inspiring"], "max_duration": 60, "voice": True}

    def render(self, prefs, workdir, on_stage=lambda s: None):
        from app.agents.base import Scene, Script
        from app.video.base import RenderResult
        from app.video.ffmpeg import OutputReport

        FakeVideoProvider.calls += 1
        on_stage("script")
        if FakeVideoProvider.fail_with is not None:
            raise FakeVideoProvider.fail_with
        p = Path(workdir) / "final.mp4"
        p.write_bytes(b"\x00\x00\x00\x18ftypmp42" + b"0" * 4096)
        script = Script(title=str(prefs.get("topic")).capitalize(), scenes=[Scene(narration="hi", on_screen_text="hi", visual_query="x")])
        return RenderResult(str(p), 720, 1280, float(prefs["duration_seconds"]), script, None,
                            OutputReport(ok=True, width=720, height=1280, duration=prefs["duration_seconds"]))


@pytest.fixture
def fake_video(monkeypatch):
    FakeVideoProvider.fail_with = None
    FakeVideoProvider.calls = 0
    fake = FakeVideoProvider()
    monkeypatch.setattr(providers, "get_video_provider", lambda db: fake)
    return fake
