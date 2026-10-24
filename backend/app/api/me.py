from __future__ import annotations

import secrets
from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.api.auth import _send_verification
from app.api.deps import CSRF_COOKIE, SESSION_COOKIE, client_ip, current_user, rate_limit
from app.api.serializers import iso, user_out
from app.config import get_settings
from app.conversation.engine import link_code_hash
from app.db import get_db
from app.models import (
    Conversation,
    GeneratedAsset,
    LoginChallenge,
    UsageRecord,
    User,
    WhatsAppAccount,
    utcnow,
)
from app.security import hash_password, verify_password, wa_id_from_e164
from app.services import login as login_svc
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
    if user.email and user.email_verified_at is None:
        raise HTTPException(403, "Verify your email address first")
    wa_id = wa_id_from_e164(user.phone_e164)
    wa = db.scalar(select(WhatsAppAccount).where(WhatsAppAccount.wa_id == wa_id))
    if wa and wa.user_id and wa.user_id != user.id:
        raise HTTPException(409, "This WhatsApp number is linked to another account")
    if wa and wa.user_id == user.id:
        return {"status": "linked"}
    if wa is None:
        wa = WhatsAppAccount(wa_id=wa_id, status="unlinked")
        db.add(wa)
        db.flush()
        db.add(Conversation(whatsapp_account_id=wa.id))
    code = f"{secrets.randbelow(1_000_000):06d}"
    wa.pending_user_id = user.id
    wa.status = "pending"
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
    wa = db.scalar(select(WhatsAppAccount).where(WhatsAppAccount.user_id == user.id))
    if wa is None:
        return {"ok": True}
    if not (user.email and user.password_hash):
        raise HTTPException(409, "WhatsApp is your only way to sign in. Add an email and password first.")
    wa.user_id = None
    wa.status = "unlinked"
    conv = db.scalar(select(Conversation).where(Conversation.whatsapp_account_id == wa.id))
    if conv:
        conv.user_id = None
        conv.active_project_id = None
    audit(db, "whatsapp.unlinked", actor_id=user.id, target_type="whatsapp_account", target_id=wa.id,
          ip=client_ip(request))
    db.commit()
    return {"ok": True}


@router.post("/reauth/start", dependencies=[Depends(rate_limit("reauth", 10, 3600))])
def reauth_start(user: User = Depends(current_user), db: Session = Depends(get_db)):
    """Fresh proof for sensitive actions: send `LOGIN <code>` from the linked WhatsApp number."""
    wa = db.scalar(select(WhatsAppAccount).where(WhatsAppAccount.user_id == user.id,
                                                 WhatsAppAccount.status == "linked"))
    if wa is None:
        raise HTTPException(409, "Link your WhatsApp number first")
    ch, code, _ = login_svc.create_challenge(db, wa.wa_id, "reauth", user=user)
    db.commit()
    from app.api.auth import challenge_instructions
    return challenge_instructions(ch, code)


@router.get("/reauth/{challenge_id}")
def reauth_status(challenge_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    ch = db.get(LoginChallenge, challenge_id)
    if ch is None or ch.purpose != "reauth" or ch.user_id != user.id:
        raise HTTPException(404, "Unknown check")
    if ch.status == "pending" and login_svc.is_expired(ch):
        return {"status": "expired"}
    return {"status": ch.status}


def _require_proof(db: Session, user: User, password: str | None, reauth_challenge_id: str | None) -> None:
    """Sensitive actions need the password (if the account has one) or a fresh WhatsApp check."""
    if password is not None and user.password_hash:
        if verify_password(user.password_hash, password):
            return
        raise HTTPException(403, "Password is incorrect")
    if login_svc.redeem_reauth(db, user, reauth_challenge_id):
        return
    raise HTTPException(403, "Confirm it's you with your password or a WhatsApp check")


class CredentialsIn(BaseModel):
    email: EmailStr
    password: str = Field(min_length=10, max_length=200)
    reauth_challenge_id: str | None = Field(default=None, max_length=36)


@router.post("/credentials", dependencies=[Depends(rate_limit("credentials", 5, 3600))])
def add_credentials(body: CredentialsIn, request: Request, user: User = Depends(current_user),
                    db: Session = Depends(get_db)):
    """WhatsApp-only account adds email + password login. Never merges accounts."""
    if user.password_hash:
        raise HTTPException(409, "This account already has a password")
    email = body.email.lower()
    other = db.scalar(select(User).where(User.email == email, User.id != user.id))
    if other:
        raise HTTPException(409, "An account with this email already exists")
    _require_proof(db, user, None, body.reauth_challenge_id)
    user.email, user.password_hash, user.email_verified_at = email, hash_password(body.password), None
    audit(db, "user.credentials_added", actor_id=user.id, target_type="user", target_id=user.id,
          ip=client_ip(request))
    verify_url = _send_verification(user)
    db.commit()
    out = {"user": user_out(db, user)}
    if get_settings().is_dev:
        out["dev_verification_url"] = verify_url
    return out


class DeleteAccountIn(BaseModel):
    password: str | None = Field(default=None, max_length=200)
    reauth_challenge_id: str | None = Field(default=None, max_length=36)


@router.post("/delete", dependencies=[Depends(rate_limit("delete", 5, 3600))])
def delete_account(body: DeleteAccountIn, request: Request, response: Response,
                   user: User = Depends(current_user), db: Session = Depends(get_db)):
    """Right to erasure: removes the account, conversations, messages, projects and files."""
    _require_proof(db, user, body.password, body.reauth_challenge_id)
    storage = get_storage()
    for a in db.scalars(select(GeneratedAsset).where(GeneratedAsset.user_id == user.id)).all():
        try:
            storage.delete(a.storage_key)
        except Exception:  # noqa: BLE001
            pass
    wa = db.scalar(select(WhatsAppAccount).where(WhatsAppAccount.user_id == user.id))
    if wa:
        db.execute(delete(Conversation).where(Conversation.whatsapp_account_id == wa.id))
        db.delete(wa)
    uid = user.id
    db.execute(delete(User).where(User.id == uid))
    audit(db, "user.deleted", actor_id=uid, target_type="user", target_id=uid, ip=client_ip(request))
    db.commit()
    response.delete_cookie(SESSION_COOKIE, path="/")
    response.delete_cookie(CSRF_COOKIE, path="/")
    return {"ok": True}
