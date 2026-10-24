"""Development-only WhatsApp simulator (404 when APP_ENV=production).

Messages go through the real pipeline: Cloud-API-shaped payload → HMAC signature →
webhook ingest → tasks. Logged-in users send as their registered number; without a
session you pick a test number (to try the anonymous first-draft flow).
"""
from __future__ import annotations

import json
import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import _session_from_request
from app.api.serializers import iso
from app.api.webhooks import process_webhook_body
from app.config import get_settings
from app.db import get_db
from app.messaging.base import build_inbound_payload
from app.models import Conversation, GeneratedAsset, Message, User, WhatsAppAccount
from app.security import meta_signature, normalize_phone, sign_asset_url, verify_meta_signature, wa_id_from_e164
from app.services.email import DEV_OUTBOX
from app.storage import get_storage
from app.transcription import MockTranscriptionProvider

router = APIRouter(prefix="/api/dev", tags=["dev"])
MEDIA_MIME = {".mp4": "video/mp4", ".mov": "video/quicktime", ".3gp": "video/3gpp", ".webm": "video/webm",
              ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png", ".webp": "image/webp"}


def dev_only() -> None:
    if not get_settings().is_dev:
        raise HTTPException(404, "Not found")


def sender(request: Request, db: Session, phone: str | None) -> tuple[str, str]:
    sess = _session_from_request(request, db)
    if sess is not None:
        user = db.get(User, sess.user_id)
        if user and user.phone_e164:
            return wa_id_from_e164(user.phone_e164), user.name
    e164 = normalize_phone(phone or "")
    if not e164:
        raise HTTPException(422, "Log in, or give a test phone number in international format")
    return wa_id_from_e164(e164), "Test user"


def _post(db: Session, payload: dict) -> dict:
    raw = json.dumps(payload).encode()
    secret = get_settings().whatsapp_app_secret
    assert verify_meta_signature(raw, meta_signature(raw, secret), secret)
    return process_webhook_body(db, json.loads(raw))


def _store_media(data: bytes, mime: str) -> str:
    media_id = f"devmedia{uuid.uuid4().hex}"
    st = get_storage()
    st.put_bytes(f"dev-media/{media_id}", data, mime)
    st.put_bytes(f"dev-media/{media_id}.mime", mime.encode(), "text/plain")
    return media_id


class SimText(BaseModel):
    text: str | None = Field(default=None, max_length=4000)
    button_id: str | None = Field(default=None, max_length=200)
    button_title: str | None = Field(default=None, max_length=40)
    phone: str | None = Field(default=None, max_length=24)


@router.get("/info", dependencies=[Depends(dev_only)])
def info():
    from app.pipeline.registry import describe

    s = get_settings()
    return {"dev": True, "messaging_provider": s.messaging_provider, "ai_provider": s.ai_provider,
            "tts_provider": s.tts_provider, "pipeline": describe()}


@router.post("/whatsapp/send", dependencies=[Depends(dev_only)])
def sim_send(body: SimText, request: Request, db: Session = Depends(get_db)):
    if not body.text and not body.button_id:
        raise HTTPException(422, "text or button_id required")
    wa_id, name = sender(request, db, body.phone)
    return _post(db, build_inbound_payload(wa_id, f"wamid.sim.{uuid.uuid4().hex}", text=body.text,
                                           button_id=body.button_id, button_title=body.button_title or "",
                                           profile_name=name))


@router.post("/whatsapp/media", dependencies=[Depends(dev_only)])
async def sim_media(request: Request, files: list[UploadFile] = File(...), caption: str = Form("", max_length=1000),
                    phone: str = Form("", max_length=24), db: Session = Depends(get_db)):
    wa_id, name = sender(request, db, phone)
    sent = 0
    for i, f in enumerate(files[:20]):
        ext = Path(f.filename or "").suffix.lower()
        mime = (f.content_type or MEDIA_MIME.get(ext, "")).split(";")[0]
        kind = "video" if mime.startswith("video/") else "image" if mime.startswith("image/") else None
        if kind is None:
            continue
        data = await f.read(get_settings().max_video_bytes + 1)
        mid = _store_media(data, mime)
        _post(db, build_inbound_payload(wa_id, f"wamid.sim.{uuid.uuid4().hex}", media_type=kind, media_id=mid,
                                        media_mime=mime, caption=caption if i == len(files) - 1 else None,
                                        profile_name=name))
        sent += 1
    return {"sent": sent}


@router.post("/whatsapp/samples", dependencies=[Depends(dev_only)])
def sim_samples(request: Request, phone: str | None = None, db: Session = Depends(get_db)):
    """Send the generated sample footage (synthetic — for demo mode only)."""
    from app.media.samples import sample_files

    wa_id, name = sender(request, db, phone)
    files = sample_files()
    for path, mime in files:
        mid = _store_media(Path(path).read_bytes(), mime)
        _post(db, build_inbound_payload(wa_id, f"wamid.sim.{uuid.uuid4().hex}",
                                        media_type="video" if mime.startswith("video/") else "image",
                                        media_id=mid, media_mime=mime, profile_name=name))
    return {"sent": len(files)}


@router.post("/whatsapp/voice", dependencies=[Depends(dev_only)])
async def sim_voice(request: Request, file: UploadFile = File(...), transcript: str = Form("", max_length=2000),
                    phone: str = Form("", max_length=24), db: Session = Depends(get_db)):
    wa_id, name = sender(request, db, phone)
    data = await file.read(get_settings().max_voice_note_bytes + 1)
    mime = (file.content_type or "audio/ogg").split(";")[0]
    if get_settings().transcription_provider == "mock" and transcript.strip():
        data = MockTranscriptionProvider.embed(data, transcript.strip())
    mid = _store_media(data, mime)
    return _post(db, build_inbound_payload(wa_id, f"wamid.sim.{uuid.uuid4().hex}", audio_media_id=mid,
                                           audio_mime=mime, profile_name=name))


@router.get("/whatsapp/messages", dependencies=[Depends(dev_only)])
def sim_messages(request: Request, phone: str | None = None, db: Session = Depends(get_db)):
    wa_id, _ = sender(request, db, phone)
    wa = db.scalar(select(WhatsAppAccount).where(WhatsAppAccount.wa_id == wa_id))
    conv = db.scalar(select(Conversation).where(Conversation.whatsapp_account_id == wa.id)) if wa else None
    if conv is None:
        return {"items": [], "linked": False, "wa_id": wa_id}
    rows = db.scalars(select(Message).where(Message.conversation_id == conv.id)
                      .order_by(Message.created_at.desc()).limit(150)).all()
    items = []
    for m in reversed(rows):
        p = m.payload or {}
        item = {"id": m.id, "direction": m.direction, "kind": m.kind, "text": m.body, "transcript": m.transcript,
                "status": m.status, "error": m.error, "at": iso(m.created_at), "buttons": p.get("buttons"),
                "via_template": p.get("via_template") or p.get("template_sent")}
        if m.kind in ("video", "link") and p.get("asset_id"):
            a = db.get(GeneratedAsset, p["asset_id"])
            if a and not a.deleted_at:
                item["video_url"] = sign_asset_url(a.id, 3600) + "&inline=1"
                item["is_demo"] = a.is_demo
        items.append(item)
    return {"items": items, "linked": wa.status == "linked", "wa_id": wa_id}


@router.get("/emails", dependencies=[Depends(dev_only)])
def dev_emails():
    return {"items": DEV_OUTBOX[-10:]}
