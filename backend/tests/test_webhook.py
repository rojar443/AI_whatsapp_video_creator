import json
import uuid

from app.jobs.tasks import run_pending
from app.messaging.base import build_inbound_payload, parse_webhook
from app.models import Message, TaskQueue
from tests.conftest import last_reply, outbound, post_webhook, wa_text

WA = "15557654321"


def test_verification_handshake(client):
    ok = client.get("/api/webhooks/whatsapp", params={"hub.mode": "subscribe", "hub.verify_token": "test-verify-token",
                                                      "hub.challenge": "12345"})
    assert ok.status_code == 200 and ok.text == "12345"
    bad = client.get("/api/webhooks/whatsapp", params={"hub.mode": "subscribe", "hub.verify_token": "wrong",
                                                       "hub.challenge": "1"})
    assert bad.status_code == 403


def test_rejects_bad_or_missing_signature(client, db):
    payload = build_inbound_payload(WA, "wamid.x", text="hi")
    raw = json.dumps(payload).encode()
    assert client.post("/api/webhooks/whatsapp", content=raw).status_code == 401
    assert post_webhook(client, payload, secret="attacker-secret").status_code == 401
    assert db.query(Message).count() == 0


def test_unknown_number_gets_signup_prompt_once(client, db):
    assert wa_text(client, WA, "hello").status_code == 200
    run_pending(db)
    assert "isn't linked" in last_reply(db, WA).body
    wa_text(client, WA, "hello again")
    run_pending(db)
    assert len(outbound(db, WA)) == 1  # throttled: no repeated prompts


def test_duplicate_delivery_is_idempotent(client, db):
    for _ in range(3):
        r = wa_text(client, WA, "hi", msg_id="wamid.same")
        assert r.status_code == 200
    assert db.query(Message).filter_by(direction="inbound").count() == 1
    assert db.query(TaskQueue).filter_by(task_type="process_inbound").count() == 1
    run_pending(db)
    assert len(outbound(db, WA)) == 1


def test_malformed_payloads_are_ignored(client):
    for body in ({}, {"entry": "x"}, {"entry": [{"changes": [{"value": {"messages": [{"id": "1"}]}}]}]}, []):
        assert post_webhook(client, body).status_code == 200
    raw = b"not json"
    from app.security import meta_signature

    r = client.post("/api/webhooks/whatsapp", content=raw,
                    headers={"x-hub-signature-256": meta_signature(raw, "test-app-secret")})
    assert r.status_code == 400


def test_status_updates_progress_outbound_messages(client, db):
    wa_text(client, WA, "hi")
    run_pending(db)
    msg = last_reply(db, WA)
    assert msg.status == "sent"

    def status(s):
        return {"entry": [{"changes": [{"value": {"statuses": [
            {"id": msg.channel_message_id, "status": s, "recipient_id": WA,
             "errors": [{"code": 131026, "title": "undeliverable"}] if s == "failed" else None}]}}]}]}

    post_webhook(client, status("delivered"))
    post_webhook(client, status("sent"))  # out-of-order: must not move backwards
    db.refresh(msg)
    assert msg.status == "delivered"
    post_webhook(client, status("failed"))
    db.refresh(msg)
    assert msg.status == "failed" and "undeliverable" in msg.error


def test_parser_handles_button_and_audio():
    p = parse_webhook(build_inbound_payload(WA, "a", button_id="confirm:p:n", button_title="Go"))
    assert p.messages[0].kind == "interactive" and p.messages[0].button_id == "confirm:p:n"
    p = parse_webhook(build_inbound_payload(WA, "b", audio_media_id="m1"))
    assert p.messages[0].kind == "audio" and p.messages[0].media_id == "m1"
    p = parse_webhook({"entry": [{"changes": [{"value": {"messages": [
        {"from": "+1 555", "id": "c", "type": "sticker", "sticker": {}}]}}]}]})
    assert p.messages[0].kind == "unsupported" and p.messages[0].wa_id == "1555"


def test_rate_limit_inbound(client, db, monkeypatch):
    from app.config import get_settings

    monkeypatch.setattr(get_settings(), "inbound_messages_per_minute", 3)
    for i in range(6):
        wa_text(client, WA, f"msg {uuid.uuid4()}")
    run_pending(db)
    bodies = [m.body for m in outbound(db, WA)]
    assert sum("very quickly" in b for b in bodies) == 1
    assert db.query(Message).filter_by(direction="inbound", status="ignored").count() >= 3
