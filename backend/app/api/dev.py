"""Development-only WhatsApp simulator. Disabled when APP_ENV=production.

Messages are sent *as the logged-in user's registered number* (never an arbitrary one)
and go through the real webhook pipeline: signed payload → signature check → ingest.
"""
from __future__ import annotations

import json
import uuid

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import current_user
from app.api.serializers import iso
from app.api.webhooks import process_webhook_body
from app.config import get_settings
from app.db import get_db
from app.messaging.base import build_inbound_payload
from app.models import Conversation, GeneratedAsset, Message, User, WhatsAppAccount
from app.security import meta_signature, sign_asset_url, verify_meta_signature, wa_id_from_e164
from app.services.email import DEV_OUTBOX
from app.storage import get_storage
from app.transcription import MockTranscriptionProvider

router = APIRouter(prefix="/api/dev", tags=["dev"])


def dev_only() -> None:
    if not get_settings().is_dev:
        raise HTTPException(404, "Not found")


def _post_signed(db: Session, payload: dict) -> dict:
    raw = json.dumps(payload).encode()
    secret = get_settings().whatsapp_app_secret
    assert verify_meta_signature(raw, meta_signature(raw, secret), secret)
    return process_webhook_body(db, json.loads(raw))


class SimText(BaseModel):
    text: str | None = Field(default=None, max_length=4000)
    button_id: str | None = Field(default=None, max_length=200)
    button_title: str | None = Field(default=None, max_length=40)


@router.get("/info", dependencies=[Depends(dev_only)])
def info():
    s = get_settings()
    return {"dev": True, "messaging_provider": s.messaging_provider, "agent_provider": s.agent_provider,
            "video_provider": s.video_provider, "transcription_provider": s.transcription_provider}


@router.post("/whatsapp/send", dependencies=[Depends(dev_only)])
def sim_send(body: SimText, user: User = Depends(current_user), db: Session = Depends(get_db)):
    if not body.text and not body.button_id:
        raise HTTPException(422, "text or button_id required")
    wa_id = wa_id_from_e164(user.phone_e164)
    payload = build_inbound_payload(wa_id, f"wamid.sim.{uuid.uuid4().hex}", text=body.text,
                                    button_id=body.button_id, button_title=body.button_title or "",
                                    profile_name=user.name)
    return _post_signed(db, payload)


@router.post("/whatsapp/voice", dependencies=[Depends(dev_only)])
async def sim_voice(file: UploadFile = File(...), transcript: str = Form("", max_length=2000),
                    user: User = Depends(current_user), db: Session = Depends(get_db)):
    data = await file.read(get_settings().max_voice_note_bytes + 1)
    mime = (file.content_type or "audio/ogg").split(";")[0]
    if get_settings().transcription_provider == "mock" and transcript.strip():
        data = MockTranscriptionProvider.embed(data, transcript.strip())
    media_id = f"devmedia{uuid.uuid4().hex}"
    storage = get_storage()
    storage.put_bytes(f"dev-media/{media_id}", data, mime)
    storage.put_bytes(f"dev-media/{media_id}.mime", mime.encode(), "text/plain")
    payload = build_inbound_payload(wa_id_from_e164(user.phone_e164), f"wamid.sim.{uuid.uuid4().hex}",
                                    audio_media_id=media_id, audio_mime=mime, profile_name=user.name)
    return _post_signed(db, payload)


@router.get("/whatsapp/messages", dependencies=[Depends(dev_only)])
def sim_messages(user: User = Depends(current_user), db: Session = Depends(get_db)):
    wa = db.scalar(select(WhatsAppAccount).where(WhatsAppAccount.wa_id == wa_id_from_e164(user.phone_e164)))
    if wa is None:
        return {"items": [], "linked": False}
    conv = db.scalar(select(Conversation).where(Conversation.whatsapp_account_id == wa.id))
    if conv is None:
        return {"items": [], "linked": wa.user_id == user.id}
    rows = db.scalars(select(Message).where(Message.conversation_id == conv.id)
                      .order_by(Message.created_at.desc()).limit(100)).all()
    items = []
    for m in reversed(rows):
        item = {"id": m.id, "direction": m.direction, "kind": m.kind, "text": m.body, "transcript": m.transcript,
                "status": m.status, "error": m.error, "at": iso(m.created_at),
                "buttons": (m.payload or {}).get("buttons"), "via_template": (m.payload or {}).get("via_template")}
        if m.kind in ("video", "link") and m.payload and m.payload.get("asset_id"):
            a = db.get(GeneratedAsset, m.payload["asset_id"])
            if a and a.user_id == user.id and not a.deleted_at:
                item["video_url"] = sign_asset_url(a.id, 3600) + "&inline=1"
                item["is_mock"] = a.is_mock
        items.append(item)
    return {"items": items, "linked": wa.user_id == user.id}


@router.get("/emails", dependencies=[Depends(dev_only)])
def dev_emails(user: User = Depends(current_user)):
    return {"items": [e for e in DEV_OUTBOX if e["to"] == user.email]}
