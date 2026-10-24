import json
import os
import re
import shutil
import tempfile
import uuid
from pathlib import Path

import pytest
from dotenv import dotenv_values, load_dotenv
from sqlalchemy.engine import make_url

BACKEND = Path(__file__).resolve().parents[1]
load_dotenv(BACKEND.parent / ".env")
TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL", "")
if not TEST_DATABASE_URL:
    raise RuntimeError("Set TEST_DATABASE_URL to a dedicated PostgreSQL test database before running pytest")
test_url = make_url(TEST_DATABASE_URL)
if test_url.get_backend_name() != "postgresql" or not (test_url.database or "").endswith("_test"):
    raise RuntimeError("TEST_DATABASE_URL must use PostgreSQL and a database name ending in _test; tests delete tables")
app_database_url = dotenv_values(BACKEND.parent / ".env").get("DATABASE_URL")
if app_database_url and test_url == make_url(app_database_url):
    raise RuntimeError("TEST_DATABASE_URL must differ from DATABASE_URL")
_TMP = Path(tempfile.mkdtemp(prefix="reelchat-test-"))
os.environ.update({
    "APP_ENV": "test",
    "SECRET_KEY": "test-secret-key-0123456789",
    "DATABASE_URL": TEST_DATABASE_URL,
    "TASK_BACKEND": "inline",
    "STORAGE_LOCAL_DIR": str(_TMP / "storage"),
    "WORK_DIR": str(_TMP / "work"),
    "PLAYBOOK_DIR": str(BACKEND / "playbooks"),
    "MUSIC_LIBRARY_DIR": str(_TMP / "music"),
    "MESSAGING_PROVIDER": "dev",
    "WHATSAPP_ENABLED": "true",
    "REGISTRATION_ENABLED": "true",
    "AI_PROVIDER": "rules",
    "AGENT_PROVIDER": "rules",
    "TTS_PROVIDER": "mock",
    "TRANSCRIPTION_PROVIDER": "mock",
    "WHATSAPP_APP_SECRET": "test-app-secret",
    "WHATSAPP_VERIFY_TOKEN": "test-verify-token",
    "JOB_RETRY_BASE_SECONDS": "1",
    "DEFAULT_CREDITS": "10",
    "ANON_CREDITS": "2",
    "ANON_MAX_PROJECTS": "2",
    "PUBLIC_BASE_URL": "http://testserver",
})

from fastapi.testclient import TestClient  # noqa: E402

from app import providers  # noqa: E402
from app.api.deps import limiter  # noqa: E402
from app.db import Base, get_engine, get_sessionmaker  # noqa: E402
from app.jobs.dispatch import INLINE  # noqa: E402
from app.main import app  # noqa: E402
from app.media import ffmpeg  # noqa: E402
from app.messaging.base import build_inbound_payload  # noqa: E402
from app.messaging.dev import DevMessagingProvider  # noqa: E402
from app.models import Conversation, Message, WhatsAppAccount  # noqa: E402
from app.pipeline import registry  # noqa: E402
from app.security import meta_signature  # noqa: E402
from app.storage import get_storage  # noqa: E402

HAS_FFMPEG = ffmpeg.available()
requires_ffmpeg = pytest.mark.skipif(not HAS_FFMPEG, reason="ffmpeg/ffprobe not installed")
FIXTURES = Path(__file__).parent / "fixtures"
WA = "15557654321"


@pytest.fixture(autouse=True)
def _fresh():
    engine = get_engine()
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    limiter.reset()
    providers._overrides.clear()
    registry._test_overrides.clear()
    INLINE.clear()
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


def drain(include_delayed: bool = False) -> int:
    return INLINE.drain(include_delayed=include_delayed)


# --------------------------------------------------------------------------- WhatsApp helpers
def post_webhook(client, payload: dict, secret="test-app-secret"):
    raw = json.dumps(payload).encode()
    return client.post("/api/webhooks/whatsapp", content=raw,
                       headers={"content-type": "application/json", "x-hub-signature-256": meta_signature(raw, secret)})


def wa_text(client, text: str, wa_id: str = WA, msg_id: str | None = None):
    return post_webhook(client, build_inbound_payload(wa_id, msg_id or f"wamid.{uuid.uuid4().hex}", text=text))


def wa_button(client, button_id: str, wa_id: str = WA):
    return post_webhook(client, build_inbound_payload(wa_id, f"wamid.{uuid.uuid4().hex}", button_id=button_id))


def put_media(data: bytes, mime: str) -> str:
    mid = f"m{uuid.uuid4().hex[:12]}"
    st = get_storage()
    st.put_bytes(f"dev-media/{mid}", data)
    st.put_bytes(f"dev-media/{mid}.mime", mime.encode())
    return mid


def wa_media(client, data: bytes, mime: str = "video/mp4", wa_id: str = WA, caption: str | None = None):
    mid = put_media(data, mime)
    kind = "video" if mime.startswith("video/") else "image"
    return post_webhook(client, build_inbound_payload(wa_id, f"wamid.{uuid.uuid4().hex}", media_type=kind,
                                                      media_id=mid, media_mime=mime, caption=caption))


def wa_voice(client, data: bytes, wa_id: str = WA):
    mid = put_media(data, "audio/ogg")
    return post_webhook(client, build_inbound_payload(wa_id, f"wamid.{uuid.uuid4().hex}", audio_media_id=mid))


def outbound(db, wa_id: str = WA) -> list[Message]:
    db.expire_all()
    wa = db.query(WhatsAppAccount).filter_by(wa_id=wa_id).one()
    conv = db.query(Conversation).filter_by(whatsapp_account_id=wa.id).one()
    return db.query(Message).filter_by(conversation_id=conv.id, direction="outbound").order_by(Message.created_at).all()


def bodies(db, wa_id: str = WA) -> list[str]:
    return [m.body or "" for m in outbound(db, wa_id)]


def say(client, db, text: str, wa_id: str = WA) -> list[str]:
    n = len(outbound(db, wa_id)) if _has_conv(db, wa_id) else 0
    wa_text(client, text, wa_id)
    drain()
    return [m.body or "" for m in outbound(db, wa_id)[n:]]


def _has_conv(db, wa_id):
    return db.query(WhatsAppAccount).filter_by(wa_id=wa_id).first() is not None


def button(db, prefix: str, wa_id: str = WA) -> str:
    for m in reversed(outbound(db, wa_id)):
        for b in (m.payload or {}).get("buttons", []):
            if b["id"].startswith(prefix):
                return b["id"]
    raise AssertionError(f"no {prefix} button")


def register(client, email="asha@example.com", phone="+1 555 765 4321", name="Asha Nair",
             password="correct horse battery") -> dict:
    r = client.post("/api/auth/register", json={"name": name, "email": email, "password": password, "phone": phone,
                                                  "accept_terms": True})
    assert r.status_code == 201, r.text
    data = r.json()
    client.headers["x-csrf-token"] = data["csrf_token"]
    token = re.search(r"token=([^&\s]+)", data["dev_verification_url"]).group(1)
    assert client.post("/api/auth/verify-email", json={"token": token}).status_code == 200
    return data


def link(client, db, wa_id: str = WA) -> None:
    r = client.post("/api/me/whatsapp/link")
    assert r.status_code == 200, r.text
    wa_text(client, f"LINK {r.json()['code']}", wa_id)
    drain()
    assert "Linked to" in bodies(db, wa_id)[-1]


# --------------------------------------------------------------------------- fake pipeline (no ffmpeg)
FAKE_MP4 = b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 2048
FAKE_JPG = b"\xff\xd8\xff\xe0" + b"\x00" * 2048


@pytest.fixture
def fake_pipeline(monkeypatch):
    """Replace FFmpeg-dependent pieces with fast fakes; the real orchestration still runs."""
    from app.media.intake import MediaInfo
    from app.pipeline import contracts as C

    calls = {"render": 0, "fail_render": None}

    def fake_inspect(path, mime, size):
        from app.media.intake import MediaRejected, base_mime

        m = base_mime(mime)
        if m.startswith("video/"):
            return MediaInfo("video", "mp4", 8.0, 1920, 1080, "h264", True)
        if m.startswith("image/"):
            return MediaInfo("photo", "jpg", None, 1600, 1200, "mjpeg", False)
        raise MediaRejected("unsupported file type (send videos or photos)")

    monkeypatch.setattr("app.jobs.tasks.inspect", fake_inspect)

    class Q:
        def run(self, inp):
            return C.QualityOutput(results=[C.AssetQuality(asset_id=a.asset_id, keep=True, score=0.9)
                                            for a in inp.assets])

    class Scenes:
        def run(self, inp):
            segs = []
            for a in inp.assets:
                if a.kind == "photo":
                    segs.append(C.Segment(segment_id=f"{a.asset_id}:0", asset_id=a.asset_id, kind="photo", end=0))
                else:
                    segs += [C.Segment(segment_id=f"{a.asset_id}:{i}", asset_id=a.asset_id, kind="video",
                                       start=i * 4.0, end=i * 4.0 + 4.0) for i in range(2)]
            return C.SceneSplitOutput(segments=segs)

    class Scorer:
        def run(self, inp):
            return C.ScoringOutput(scores=[C.SegmentScore(segment_id=s.segment_id, score=9 - i * 0.3,
                                                          description=f"shot {i}", tags=["landscape"] if i == 0 else [])
                                           for i, s in enumerate(inp.segments)], scorer="fake")

    class Narr:
        def run(self, inp):
            p = Path(inp.work_dir) / "n.wav"
            p.write_bytes(b"RIFF0000")
            return C.NarrationOutput(audio_path=str(p), duration=5, provider="fake")

    class Music:
        def run(self, inp):
            return C.MusicOutput(audio_path=None, title="Test track", license="test", generated=True)

    class Render:
        def run(self, inp):
            calls["render"] += 1
            if calls["fail_render"] is not None:
                raise calls["fail_render"]
            p = Path(inp.work_dir) / "reel.mp4"
            p.write_bytes(FAKE_MP4)
            total = sum(i.source_end - i.source_start for i in inp.items)
            return C.RenderOutput(video_path=str(p), duration=total, width=inp.width, height=inp.height,
                                  size_bytes=len(FAKE_MP4))

    for name, obj in {"quality_filter": Q(), "scene_split": Scenes(), "moment_scoring": Scorer(),
                      "narration": Narr(), "music": Music(), "render": Render()}.items():
        obj.name, obj.impl = name, "builtin"  # stands in for the builtin implementation
        registry._test_overrides[name] = obj
    monkeypatch.setattr("app.pipeline.run.fit_narration", lambda path, m, words, wd: (path, words))

    def fake_validate(path, w, h, seconds, tolerance=1.0):
        return ffmpeg.OutputReport(ok=True, width=w, height=h, duration=seconds, size_bytes=len(FAKE_MP4),
                                   has_audio=True, video_codec="h264", audio_codec="aac")

    monkeypatch.setattr("app.pipeline.run.ffmpeg.validate_mp4", fake_validate)
    return calls


def send_batch(client, n_videos=3, n_photos=1, wa_id=WA):
    for _ in range(n_videos):
        wa_media(client, FAKE_MP4, "video/mp4", wa_id)
    for _ in range(n_photos):
        wa_media(client, FAKE_JPG, "image/jpeg", wa_id)
    drain()
    INLINE.run_delayed("batch_ack")  # the debounced "Got N clips" fires a few seconds later
