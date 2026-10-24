from __future__ import annotations

import secrets
from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.api.deps import CSRF_COOKIE, SESSION_COOKIE, client_ip, current_user, rate_limit
from app.api.serializers import iso, user_out
from app.config import get_settings
from app.conversation.engine import link_code_hash
from app.db import get_db
from app.models import Conversation, GeneratedAsset, UsageRecord, User, WhatsAppAccount, utcnow
from app.security import verify_password, wa_id_from_e164
from app.services.audit import audit
from app.storage import get_storage

router = APIRouter(prefix="/api/me", tags=["account"])


@router.get("")
def me(user: User = Depends(current_user), db: Session = Depends(get_db)):
    return user_out(db, user)


@router.get("/usage")
def usage(user: User = Depends(current_user), db: Session = Depends(get_db)):
    rows = db.scalars(select(UsageRecord).where(UsageRecord.user_id == user.id, UsageRecord.credits_delta != 0)
                      .order_by(UsageRecord.created_at.desc()).limit(50)).all()
    return {
        "credits": user.credits_balance,
        "max_jobs_per_day": get_settings().max_jobs_per_day,
        "ledger": [{"kind": r.kind, "credits": r.credits_delta, "job_id": r.job_id, "at": iso(r.created_at)}
                   for r in rows],
    }


@router.post("/whatsapp/link", dependencies=[Depends(rate_limit("linkcode", 10, 3600))])
def start_link(request: Request, user: User = Depends(current_user), db: Session = Depends(get_db)):
    """Issue a 6-digit code. The user sends 'LINK <code>' from their registered WhatsApp
    number: that proves they own the number and opts them in to messages from us."""
    s = get_settings()
    if not s.whatsapp_enabled:
        raise HTTPException(404, "WhatsApp is not enabled on this server")
    if not user.phone_e164:
        raise HTTPException(422, "Add your WhatsApp number first")
    wa_id = wa_id_from_e164(user.phone_e164)
    wa = db.scalar(select(WhatsAppAccount).where(WhatsAppAccount.wa_id == wa_id))
    if wa and wa.user_id and wa.user_id != user.id:
        owner = db.get(User, wa.user_id)
        if owner is not None and not owner.is_anonymous:  # anonymous drafts get merged on link
            raise HTTPException(409, "This WhatsApp number is linked to another account")
    if wa and wa.user_id == user.id and wa.status == "linked":
        return {"status": "linked"}
    if wa is None:
        wa = WhatsAppAccount(wa_id=wa_id, status="anonymous")
        db.add(wa)
        db.flush()
        db.add(Conversation(whatsapp_account_id=wa.id))
    code = f"{secrets.randbelow(1_000_000):06d}"
    wa.pending_user_id = user.id
    wa.link_code_hash = link_code_hash(wa_id, code)
    wa.link_code_expires_at = utcnow() + timedelta(minutes=s.link_code_ttl_minutes)
    wa.link_attempts = 0
    audit(db, "whatsapp.link_code_issued", actor_id=user.id, target_type="whatsapp_account", target_id=wa.id,
          ip=client_ip(request))
    db.commit()
    text = f"LINK {code}"
    return {
        "status": "pending",
        "code": code,
        "expires_in_minutes": s.link_code_ttl_minutes,
        "send_from": user.phone_e164,
        "business_number": s.whatsapp_business_number,
        "wa_me_url": f"https://wa.me/{s.whatsapp_business_number}?text=LINK%20{code}",
        "message": text,
    }


@router.delete("/whatsapp")
def unlink(request: Request, user: User = Depends(current_user), db: Session = Depends(get_db)):
    return _unlink(request, user, db, "whatsapp", WhatsAppAccount.wa_id.not_like("tg:%"))


def _unlink(request: Request, user: User, db: Session, label: str, cond):
    wa = db.scalar(select(WhatsAppAccount).where(WhatsAppAccount.user_id == user.id, cond))
    if wa is None:
        return {"ok": True}
    wa.user_id = None
    wa.status = "anonymous"
    conv = db.scalar(select(Conversation).where(Conversation.whatsapp_account_id == wa.id))
    if conv:
        conv.user_id = None
        conv.active_project_id = None
    audit(db, f"{label}.unlinked", actor_id=user.id, target_type="whatsapp_account", target_id=wa.id,
          ip=client_ip(request))
    db.commit()
    return {"ok": True}


@router.post("/telegram/link", dependencies=[Depends(rate_limit("tglink", 10, 3600))])
def start_telegram_link(user: User = Depends(current_user), db: Session = Depends(get_db)):
    """Deep link `t.me/<bot>?start=link_<signed token>`: opening it in Telegram links that chat to this account."""
    from app import providers
    from app.messaging.base import SendError
    from app.security import sign_payload

    tg = providers.get_telegram()
    if tg is None:
        raise HTTPException(503, "Telegram is not configured on this server")
    linked = db.scalar(select(WhatsAppAccount).where(WhatsAppAccount.user_id == user.id,
                                                     WhatsAppAccount.wa_id.like("tg:%"), WhatsAppAccount.status == "linked"))
    if linked:
        return {"status": "linked"}
    try:
        username = tg.me().get("username")
    except SendError as e:
        raise HTTPException(502, "Could not reach Telegram") from e
    token = sign_payload({"uid": user.id}, "telegram-link", get_settings().link_code_ttl_minutes * 60)
    return {"status": "pending", "bot": "@" + username, "url": f"https://t.me/{username}?start=link_{token}",
            "expires_in_minutes": get_settings().link_code_ttl_minutes}


@router.delete("/telegram")
def unlink_telegram(request: Request, user: User = Depends(current_user), db: Session = Depends(get_db)):
    return _unlink(request, user, db, "telegram", WhatsAppAccount.wa_id.like("tg:%"))


class DeleteAccountIn(BaseModel):
    password: str = Field(max_length=200)


@router.post("/delete", dependencies=[Depends(rate_limit("delete", 5, 3600))])
def delete_account(body: DeleteAccountIn, request: Request, response: Response,
                   user: User = Depends(current_user), db: Session = Depends(get_db)):
    """Right to erasure: removes the account, conversations, messages, projects and files."""
    if not verify_password(user.password_hash, body.password):
        raise HTTPException(403, "Password is incorrect")
    storage = get_storage()
    for a in db.scalars(select(GeneratedAsset).where(GeneratedAsset.user_id == user.id)).all():
        try:
            storage.delete(a.storage_key)
        except Exception:  # noqa: BLE001
            pass
    for wa in db.scalars(select(WhatsAppAccount).where(WhatsAppAccount.user_id == user.id)).all():
        db.execute(delete(Conversation).where(Conversation.whatsapp_account_id == wa.id))
        db.delete(wa)
    uid = user.id
    db.execute(delete(User).where(User.id == uid))
    audit(db, "user.deleted", actor_id=uid, target_type="user", target_id=uid, ip=client_ip(request))
    db.commit()
    response.delete_cookie(SESSION_COOKIE, path="/")
    response.delete_cookie(CSRF_COOKIE, path="/")
    return {"ok": True}
