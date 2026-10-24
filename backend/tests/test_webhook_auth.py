"""Webhook security/idempotency, auth, CSRF, linking and account deletion."""
import json

from app.messaging.base import build_inbound_payload, parse_webhook
from app.models import Message, User, VideoProject
from app.security import meta_signature, sign_asset_url
from tests.conftest import WA, bodies, drain, fresh_client, link, post_webhook, register, say, send_batch, wa_text


def test_verification_handshake(client):
    ok = client.get("/api/webhooks/whatsapp", params={"hub.mode": "subscribe", "hub.verify_token": "test-verify-token",
                                                      "hub.challenge": "42"})
    assert ok.status_code == 200 and ok.text == "42"
    assert client.get("/api/webhooks/whatsapp", params={"hub.mode": "subscribe", "hub.verify_token": "x",
                                                        "hub.challenge": "1"}).status_code == 403


def test_rejects_unsigned_or_forged(client, db):
    payload = build_inbound_payload(WA, "wamid.x", text="hi")
    assert client.post("/api/webhooks/whatsapp", content=json.dumps(payload).encode()).status_code == 401
    assert post_webhook(client, payload, secret="attacker").status_code == 401
    assert db.query(Message).count() == 0


def test_duplicate_deliveries_are_idempotent(client, db):
    for _ in range(3):
        assert wa_text(client, "hi", msg_id="wamid.same").status_code == 200
    drain()
    assert db.query(Message).filter_by(direction="inbound").count() == 1
    assert len(bodies(db)) == 2  # first-contact notice + one reply


def test_parser_media_document_and_caption():
    p = parse_webhook({"entry": [{"changes": [{"value": {"messages": [
        {"from": "15550001", "id": "1", "type": "video", "video": {"id": "m1", "mime_type": "video/mp4", "caption": "hi"}},
        {"from": "15550001", "id": "2", "type": "document", "document": {"id": "m2", "mime_type": "video/quicktime"}},
        {"from": "15550001", "id": "3", "type": "image", "image": {"id": "m3", "mime_type": "image/jpeg"}},
        {"from": "15550001", "id": "4", "type": "sticker", "sticker": {"id": "m4"}},
    ]}}]}]})
    kinds = [m.kind for m in p.messages]
    assert kinds == ["video", "video", "image", "unsupported"] and p.messages[0].caption == "hi"


def test_malformed_payloads(client):
    for body in ({}, {"entry": "x"}, {"entry": [{"changes": [{"value": {"messages": [{"id": "1"}]}}]}]}):
        assert post_webhook(client, body).status_code == 200
    raw = b"not json"
    assert client.post("/api/webhooks/whatsapp", content=raw,
                       headers={"x-hub-signature-256": meta_signature(raw, "test-app-secret")}).status_code == 400


def test_register_login_csrf(client):
    register(client)
    token = client.headers.pop("x-csrf-token")
    assert client.post("/api/me/whatsapp/link").status_code == 403
    assert client.post("/api/me/whatsapp/link", headers={"x-csrf-token": token}).status_code == 200
    client.headers["x-csrf-token"] = token
    assert client.post("/api/auth/logout").status_code == 200
    assert client.get("/api/me").status_code == 401
    assert client.post("/api/auth/login", json={"email": "asha@example.com", "password": "nope-nope"}).status_code == 401


def test_link_code_must_come_from_registered_number(client, db):
    register(client)
    code = client.post("/api/me/whatsapp/link").json()["code"]
    wa_text(client, f"LINK {code}", wa_id="15550009999")  # someone else's phone
    drain()
    assert "didn't match" in bodies(db, "15550009999")[-1]
    assert client.get("/api/me").json()["whatsapp"]["status"] == "pending"
    for _ in range(5):
        wa_text(client, "LINK 000000")
    wa_text(client, f"LINK {code}")  # locked after 5 wrong attempts
    drain()
    assert client.get("/api/me").json()["whatsapp"]["status"] == "pending"


def test_number_owned_by_registered_user_cannot_be_claimed(client, db):
    register(client)
    link(client, db)
    mallory = fresh_client()
    register(mallory, email="m@example.com", phone="+1 555 765 4321")
    assert mallory.post("/api/me/whatsapp/link").status_code == 409


def test_signed_download_links(client, db, fake_pipeline):
    send_batch(client, 3, 0)
    say(client, db, "that's all, travel reel")
    say(client, db, "yes")
    from app.models import GeneratedAsset

    asset = db.query(GeneratedAsset).filter_by(kind="video").one()
    url = sign_asset_url(asset.id).replace("http://testserver", "")
    anon = fresh_client()
    assert anon.get(url).status_code == 200
    assert anon.get(url[:-3] + "zzz").status_code == 403
    expired = sign_asset_url(asset.id, ttl_seconds=-5).replace("http://testserver", "")
    assert anon.get(expired).status_code == 410


def test_pipeline_file_links_are_confined(client, tmp_path):
    from app.config import get_settings
    from app.security import sign_pipeline_file

    outside = tmp_path / "secret.txt"
    outside.write_text("nope")
    assert client.get(sign_pipeline_file(str(outside)).replace("http://testserver", "")).status_code == 404
    assert client.get(sign_pipeline_file("/etc/passwd").replace("http://testserver", "")).status_code == 404
    from pathlib import Path

    ok = Path(get_settings().work_dir) / "share.txt"
    ok.parent.mkdir(parents=True, exist_ok=True)
    ok.write_text("fine")
    assert client.get(sign_pipeline_file(str(ok)).replace("http://testserver", "")).text == "fine"
    assert client.get("/api/pipeline-files/forged.token").status_code == 403


def test_account_deletion(client, db, fake_pipeline):
    register(client)
    link(client, db)
    send_batch(client, 2, 0)
    say(client, db, "that's all, travel reel")
    assert client.post("/api/me/delete", json={"password": "wrong-one!"}).status_code == 403
    assert client.post("/api/me/delete", json={"password": "correct horse battery"}).status_code == 200
    db.expire_all()
    assert db.query(User).filter_by(is_anonymous=False).count() == 0
    assert db.query(VideoProject).count() == 0
