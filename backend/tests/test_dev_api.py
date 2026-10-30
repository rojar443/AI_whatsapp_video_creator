"""Development WhatsApp simulator endpoints (/api/dev/*)."""
import pytest

from app.config import get_settings
from app.models import Conversation, GeneratedAsset, Message, User, WhatsAppAccount
from app.services import email
from app.storage import get_storage
from app.transcription import MockTranscriptionProvider
from tests.conftest import FAKE_JPG, FAKE_MP4, WA, register

PHONE = "+1 555 765 4321"


def inbound(db, wa_id=WA):
    db.expire_all()
    wa = db.query(WhatsAppAccount).filter_by(wa_id=wa_id).one()
    conv = db.query(Conversation).filter_by(whatsapp_account_id=wa.id).one()
    return db.query(Message).filter_by(conversation_id=conv.id, direction="inbound").order_by(Message.created_at).all()


@pytest.mark.parametrize("method,path", [
    ("get", "/api/dev/info"), ("post", "/api/dev/whatsapp/send"), ("get", "/api/dev/whatsapp/messages"),
    ("get", "/api/dev/emails"), ("post", "/api/dev/whatsapp/samples"),
])
def test_dev_routes_hidden_in_production(client, monkeypatch, method, path):
    monkeypatch.setattr(get_settings(), "app_env", "production")
    assert getattr(client, method)(path).status_code == 404


def test_info(client):
    r = client.get("/api/dev/info").json()
    assert r["dev"] is True and r["ai_provider"] == "rules" and "pipeline" in r


def test_send_requires_text_or_button(client):
    assert client.post("/api/dev/whatsapp/send", json={"phone": PHONE}).status_code == 422


def test_send_requires_login_or_phone(client):
    r = client.post("/api/dev/whatsapp/send", json={"text": "hi"})
    assert r.status_code == 422 and "test phone" in r.json()["detail"]
    assert client.post("/api/dev/whatsapp/send", json={"text": "hi", "phone": "abc"}).status_code == 422


def test_send_text_as_test_number(client, db):
    assert client.post("/api/dev/whatsapp/send", json={"text": "hello", "phone": PHONE}).status_code == 200
    msgs = inbound(db)
    assert len(msgs) == 1 and msgs[0].body == "hello"


def test_send_button(client, db):
    client.post("/api/dev/whatsapp/send", json={"button_id": "confirm", "button_title": "Yes", "phone": PHONE})
    assert inbound(db)[0].kind == "interactive"


def test_send_uses_logged_in_users_number(client, db):
    register(client, phone="+1 555 000 1111")
    client.post("/api/dev/whatsapp/send", json={"text": "as me", "phone": PHONE})
    assert inbound(db, "15550001111")[0].body == "as me"
    assert db.query(WhatsAppAccount).filter_by(wa_id=WA).first() is None


def test_send_logged_in_without_phone_falls_back(client, db):
    register(client)
    user = db.query(User).one()
    user.phone_e164 = None
    db.commit()
    assert client.post("/api/dev/whatsapp/send", json={"text": "x"}).status_code == 422


def test_media_upload_filters_types_and_caption_on_last(client, db):
    files = [("files", ("a.mp4", FAKE_MP4, "video/mp4")),
             ("files", ("notes.txt", b"hello", "text/plain")),
             ("files", ("b.jpg", FAKE_JPG, "image/jpeg"))]
    r = client.post("/api/dev/whatsapp/media", files=files, data={"caption": "goa trip", "phone": PHONE})
    assert r.json() == {"sent": 2}
    msgs = inbound(db)
    assert [m.kind for m in msgs] == ["video", "image"]
    assert msgs[-1].body == "goa trip" or (msgs[-1].payload or {}).get("caption") == "goa trip"
    assert not (msgs[0].body or "")


def test_media_mime_from_extension(client, db):
    files = [("files", ("clip.MOV", FAKE_MP4, "application/octet-stream"))]
    assert client.post("/api/dev/whatsapp/media", files=files, data={"phone": PHONE}).json() == {"sent": 0}
    files = [("files", ("clip.mov", FAKE_MP4, ""))]
    r = client.post("/api/dev/whatsapp/media", files=files, data={"phone": PHONE})
    assert r.status_code == 200


def test_media_capped_at_20(client):
    files = [("files", (f"{i}.jpg", FAKE_JPG, "image/jpeg")) for i in range(22)]
    assert client.post("/api/dev/whatsapp/media", files=files, data={"phone": PHONE}).json() == {"sent": 20}


def test_voice_embeds_mock_transcript(client, db):
    r = client.post("/api/dev/whatsapp/voice", files={"file": ("v.ogg", b"OggS" + b"\x00" * 20, "audio/ogg; codecs=opus")},
                    data={"transcript": "  make a travel reel ", "phone": PHONE})
    assert r.status_code == 200
    m = inbound(db)[0]
    assert m.kind == "audio"
    mid = m.media_id
    data = get_storage().read_bytes(f"dev-media/{mid}")
    assert MockTranscriptionProvider().transcribe(data, "audio/ogg").text == "make a travel reel"
    assert get_storage().read_bytes(f"dev-media/{mid}.mime") == b"audio/ogg"


def test_voice_without_transcript_not_embedded(client, db):
    client.post("/api/dev/whatsapp/voice", files={"file": ("v.ogg", b"OggS", "audio/ogg")}, data={"phone": PHONE})
    m = inbound(db)[0]
    mid = m.media_id
    assert get_storage().read_bytes(f"dev-media/{mid}") == b"OggS"


def test_samples(client, db, monkeypatch, tmp_path):
    from app.media import samples

    v, p = tmp_path / "a.mp4", tmp_path / "b.jpg"
    v.write_bytes(FAKE_MP4)
    p.write_bytes(FAKE_JPG)
    monkeypatch.setattr(samples, "sample_files", lambda: [(str(v), "video/mp4"), (str(p), "image/jpeg")])
    assert client.post(f"/api/dev/whatsapp/samples?phone={PHONE}").json() == {"sent": 2}
    assert [m.kind for m in inbound(db)] == ["video", "image"]


def test_messages_unknown_number(client):
    r = client.get("/api/dev/whatsapp/messages", params={"phone": PHONE}).json()
    assert r == {"items": [], "linked": False, "wa_id": WA}


def test_messages_lists_conversation_and_video_links(client, db):
    client.post("/api/dev/whatsapp/send", json={"text": "hello", "phone": PHONE})
    wa = db.query(WhatsAppAccount).filter_by(wa_id=WA).one()
    conv = db.query(Conversation).filter_by(whatsapp_account_id=wa.id).one()
    user = db.get(User, wa.user_id) if wa.user_id else db.query(User).first()
    if user is None:
        user = User(name="U", status="active")
        db.add(user)
        db.flush()
    live = GeneratedAsset(user_id=user.id, kind="video", storage_key="k", mime_type="video/mp4", is_demo=True)
    gone = GeneratedAsset(user_id=user.id, kind="video", storage_key="k2", mime_type="video/mp4")
    db.add_all([live, gone])
    db.flush()
    from app.models import utcnow

    gone.deleted_at = utcnow()
    db.add_all([
        Message(conversation_id=conv.id, direction="outbound", kind="video", body="your reel",
                payload={"asset_id": live.id, "buttons": [{"id": "ok", "title": "OK"}]}, status="sent"),
        Message(conversation_id=conv.id, direction="outbound", kind="link", body="old",
                payload={"asset_id": gone.id, "template_sent": True}, status="sent"),
    ])
    db.commit()
    items = client.get("/api/dev/whatsapp/messages", params={"phone": PHONE}).json()["items"]
    reel = next(i for i in items if i["text"] == "your reel")
    old = next(i for i in items if i["text"] == "old")
    assert reel["video_url"].endswith("&inline=1") and reel["is_demo"] is True
    assert reel["buttons"] == [{"id": "ok", "title": "OK"}]
    assert "video_url" not in old and old["via_template"] is True
    assert items[0]["text"] == "hello"


def test_emails_returns_last_10(client):
    email.DEV_OUTBOX.clear()
    email.DEV_OUTBOX.extend({"to": "a", "subject": str(i), "body": ""} for i in range(15))
    items = client.get("/api/dev/emails").json()["items"]
    assert [e["subject"] for e in items] == [str(i) for i in range(5, 15)]


def test_telegram_login_blocked_user(client, db):
    assert client.post("/api/dev/telegram/login", json={"tg_id": "5551234"}).status_code == 200
    user = db.query(User).one()
    user.status = "suspended"
    db.commit()
    from tests.conftest import fresh_client

    assert fresh_client().post("/api/dev/telegram/login", json={"tg_id": "5551234"}).status_code == 403
