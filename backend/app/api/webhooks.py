"""WhatsApp Cloud API webhook: verification handshake + signed event delivery."""
from __future__ import annotations

import json
import logging

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import PlainTextResponse
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import get_db
from app.jobs.dispatch import enqueue
from app.messaging.base import InboundMessage, parse_webhook
from app.models import Conversation, Message, WhatsAppAccount, aware, utcnow
from app.security import verify_meta_signature

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api/webhooks", tags=["webhooks"])
MAX_BODY = 1024 * 1024


@router.get("/whatsapp", response_class=PlainTextResponse)
def verify(mode: str = Query(alias="hub.mode", default=""), token: str = Query(alias="hub.verify_token", default=""),
           challenge: str = Query(alias="hub.challenge", default="")):
    import hmac

    if mode == "subscribe" and hmac.compare_digest(token, get_settings().whatsapp_verify_token):
        return challenge
    raise HTTPException(403, "verification failed")


def get_or_create_conversation(db: Session, wa_id: str, profile_name: str | None) -> Conversation:
    wa = db.scalar(select(WhatsAppAccount).where(WhatsAppAccount.wa_id == wa_id))
    if wa is None:
        try:
            with db.begin_nested():
                wa = WhatsAppAccount(wa_id=wa_id, status="anonymous", profile_name=(profile_name or "")[:120] or None)
                db.add(wa)
                db.flush()
        except IntegrityError:
            wa = db.scalar(select(WhatsAppAccount).where(WhatsAppAccount.wa_id == wa_id))
    conv = db.scalar(select(Conversation).where(Conversation.whatsapp_account_id == wa.id))
    if conv is None:
        try:
            with db.begin_nested():
                conv = Conversation(whatsapp_account_id=wa.id, user_id=wa.user_id)
                db.add(conv)
                db.flush()
        except IntegrityError:
            conv = db.scalar(select(Conversation).where(Conversation.whatsapp_account_id == wa.id))
    return conv


def ingest_message(db: Session, m: InboundMessage) -> Message | None:
    """Store one inbound message and queue it. Returns None for duplicates (idempotent)."""
    if db.scalar(select(Message.id).where(Message.channel_message_id == m.message_id)):
        return None
    conv = get_or_create_conversation(db, m.wa_id, m.profile_name)
    wa = conv.whatsapp_account
    last = aware(wa.last_inbound_at)
    if last is None or m.timestamp > last:
        wa.last_inbound_at = min(m.timestamp, utcnow())
    if m.profile_name:
        wa.profile_name = m.profile_name[:120]
    payload = {k: v for k, v in {"button_id": m.button_id, "media_mime": m.media_mime, "caption": m.caption}.items() if v}
    row = Message(conversation_id=conv.id, user_id=wa.user_id, direction="inbound",
                  channel_message_id=m.message_id, kind=m.kind, body=(m.text or m.caption or "")[:4000] or None,
                  media_id=m.media_id, payload=payload or None, status="received")
    try:
        with db.begin_nested():
            db.add(row)
            db.flush()
    except IntegrityError:
        return None  # concurrent duplicate delivery
    enqueue(db, "process_inbound", row.id)  # sent after commit
    return row


def process_webhook_body(db: Session, payload: dict) -> dict:
    parsed = parse_webhook(payload)
    accepted = 0
    for m in parsed.messages:
        if ingest_message(db, m):
            accepted += 1
    for st in parsed.statuses:
        msg = db.scalar(select(Message).where(Message.channel_message_id == st.message_id))
        if msg and msg.direction == "outbound":
            order = ["pending", "sent", "delivered", "read"]
            if st.status == "failed":
                msg.status, msg.error = "failed", st.error
            elif st.status in order and order.index(st.status) > order.index(msg.status if msg.status in order else "pending"):
                msg.status = st.status
    db.commit()
    return {"accepted": accepted, "statuses": len(parsed.statuses)}


@router.post("/whatsapp")
async def receive(request: Request, db: Session = Depends(get_db)):
    raw = await request.body()
    if len(raw) > MAX_BODY:
        raise HTTPException(413, "payload too large")
    if not verify_meta_signature(raw, request.headers.get("x-hub-signature-256"), get_settings().whatsapp_app_secret):
        log.warning("rejected webhook with invalid signature")
        raise HTTPException(401, "invalid signature")
    try:
        payload = json.loads(raw or b"{}")
    except ValueError as e:
        raise HTTPException(400, "invalid JSON") from e
    # Fast ack: heavy work (transcription, LLM, replies) happens in the worker.
    return process_webhook_body(db, payload)
