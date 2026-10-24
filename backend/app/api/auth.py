from __future__ import annotations

import secrets

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.api.deps import (
    CSRF_COOKIE,
    limiter,
    SESSION_COOKIE,
    client_ip,
    current_session,
    current_user,
    rate_limit,
    session_ttl,
)
from app.api.serializers import user_out
from app.config import get_settings
from app.db import get_db
from app.models import LoginChallenge, User, UserSession, utcnow
from app.security import (
    TokenError,
    hash_password,
    new_token,
    normalize_phone,
    sha256_hex,
    sign_payload,
    verify_password,
    verify_payload,
)
from app.services import credits, login as login_svc
from app.services.audit import audit
from app.services.email import send_email

router = APIRouter(prefix="/api/auth", tags=["auth"])
# Constant-time-ish login: verify against a dummy hash when the email is unknown.
_DUMMY_HASH = hash_password(secrets.token_hex(16))


class RegisterIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    email: EmailStr
    password: str = Field(min_length=10, max_length=200)
    phone: str = Field(min_length=8, max_length=24)
    accept_terms: bool


class LoginIn(BaseModel):
    email: EmailStr
    password: str = Field(max_length=200)


class WhatsAppStartIn(BaseModel):
    phone: str = Field(min_length=8, max_length=24)
    accept_terms: bool


class TokenIn(BaseModel):
    token: str = Field(max_length=1000)


def _send_verification(user: User) -> str:
    s = get_settings()
    token = sign_payload({"uid": user.id, "email": user.email}, "verify-email", 48 * 3600)
    url = f"{s.web_base_url}/verify-email?token={token}"
    send_email(user.email, "Verify your ReelChat email",
               f"Hi {user.name},\n\nConfirm your email to start creating videos:\n{url}\n\n"
               "If you didn't sign up, ignore this message.")
    return url


def _start_session(db: Session, user: User, request: Request, response: Response) -> UserSession:
    s = get_settings()
    token = new_token()
    sess = UserSession(user_id=user.id, token_hash=sha256_hex(token), csrf_token=new_token(24),
                       ip=client_ip(request), user_agent=(request.headers.get("user-agent") or "")[:255],
                       expires_at=utcnow() + session_ttl())
    db.add(sess)
    max_age = int(session_ttl().total_seconds())
    response.set_cookie(SESSION_COOKIE, token, max_age=max_age, httponly=True, secure=s.cookie_secure,
                        samesite="lax", path="/")
    response.set_cookie(CSRF_COOKIE, sess.csrf_token, max_age=max_age, httponly=False,
                        secure=s.cookie_secure, samesite="lax", path="/")
    return sess


@router.post("/register", status_code=201, dependencies=[Depends(rate_limit("register", 5, 3600))])
def register(body: RegisterIn, request: Request, response: Response, db: Session = Depends(get_db)):
    s = get_settings()
    if not body.accept_terms:
        raise HTTPException(422, "You must accept the terms and privacy policy")
    phone = normalize_phone(body.phone)
    if phone is None:
        raise HTTPException(422, "Enter your WhatsApp number in international format, e.g. +91 98765 43210")
    email = body.email.lower()
    if db.scalar(select(User).where(User.email == email)):
        raise HTTPException(409, "An account with this email already exists")
    user = User(email=email, name=body.name.strip(), password_hash=hash_password(body.password),
                phone_e164=phone, credits_balance=0)
    if s.admin_email and email == s.admin_email.lower():
        user.role = "admin"
    db.add(user)
    db.flush()
    credits.grant(db, user, s.default_credits, key=f"signup:{user.id}")
    audit(db, "user.registered", actor_id=user.id, target_type="user", target_id=user.id, ip=client_ip(request))
    verify_url = _send_verification(user)
    sess = _start_session(db, user, request, response)
    db.commit()
    out = {"user": user_out(db, user), "csrf_token": sess.csrf_token}
    if s.is_dev:
        out["dev_verification_url"] = verify_url  # convenience: no real email in development
    return out


@router.post("/login", dependencies=[Depends(rate_limit("login", 10, 300))])
def login(body: LoginIn, request: Request, response: Response, db: Session = Depends(get_db)):
    user = db.scalar(select(User).where(User.email == body.email.lower()))
    # Unknown email and WhatsApp-only accounts (no password) take the same path and give the same error.
    has_password = bool(user and user.password_hash)
    ok = verify_password(user.password_hash if has_password else _DUMMY_HASH, body.password)
    if not has_password or not ok:
        audit(db, "auth.login_failed", details={"reason": "bad_credentials"}, ip=client_ip(request))
        db.commit()
        raise HTTPException(401, "Invalid email or password")
    if user.status != "active":
        raise HTTPException(403, "This account is suspended")
    sess = _start_session(db, user, request, response)
    audit(db, "auth.login", actor_id=user.id, ip=client_ip(request))
    db.commit()
    return {"user": user_out(db, user), "csrf_token": sess.csrf_token}


@router.post("/logout")
def logout(response: Response, sess: UserSession = Depends(current_session), db: Session = Depends(get_db)):
    sess = db.merge(sess)
    sess.revoked_at = utcnow()
    db.commit()
    response.delete_cookie(SESSION_COOKIE, path="/")
    response.delete_cookie(CSRF_COOKIE, path="/")
    return {"ok": True}


@router.post("/verify-email", dependencies=[Depends(rate_limit("verify", 20, 3600))])
def verify_email(body: TokenIn, db: Session = Depends(get_db)):
    try:
        data = verify_payload(body.token, "verify-email")
    except TokenError as e:
        raise HTTPException(400, "This link has expired" if e.reason == "expired" else "Invalid link") from e
    user = db.get(User, data.get("uid"))
    if user is None or user.email != data.get("email"):
        raise HTTPException(400, "Invalid link")
    if user.email_verified_at is None:
        user.email_verified_at = utcnow()
        audit(db, "user.email_verified", actor_id=user.id, target_type="user", target_id=user.id)
        db.commit()
    return {"ok": True}


@router.post("/resend-verification", dependencies=[Depends(rate_limit("resend", 3, 3600))])
def resend_verification(user: User = Depends(current_user)):
    if user.email_verified_at:
        return {"ok": True, "already_verified": True}
    url = _send_verification(user)
    out = {"ok": True}
    if get_settings().is_dev:
        out["dev_verification_url"] = url
    return out


# ------------------------------------------------------------------ WhatsApp login
LOGIN_COOKIE = "rc_login"  # poll secret: only the browser that asked for the code can redeem it


def challenge_instructions(ch: LoginChallenge, code: str) -> dict:
    s = get_settings()
    return {
        "challenge_id": ch.id,
        "code": code,
        "message": f"LOGIN {code}",
        "send_from": "+" + ch.wa_id,
        "business_number": s.whatsapp_business_number,
        "wa_me_url": f"https://wa.me/{s.whatsapp_business_number}?text=LOGIN%20{code}",
        "expires_in_minutes": s.link_code_ttl_minutes,
    }


@router.post("/whatsapp/start", dependencies=[Depends(rate_limit("walogin", 10, 300))])
def whatsapp_login_start(body: WhatsAppStartIn, request: Request, response: Response,
                         db: Session = Depends(get_db)):
    s = get_settings()
    if not body.accept_terms:
        raise HTTPException(422, "You must accept the terms and privacy policy")
    phone = normalize_phone(body.phone)
    if phone is None:
        raise HTTPException(422, "Enter your WhatsApp number in international format, e.g. +91 98765 43210")
    wa_id = phone.lstrip("+")
    if not limiter.hit(f"walogin-number:{wa_id}", 5, 600):
        raise HTTPException(429, "too many requests, please slow down")
    ch, code, poll_secret = login_svc.create_challenge(db, wa_id, "login", terms_accepted=True)
    db.commit()
    response.set_cookie(LOGIN_COOKIE, poll_secret, max_age=s.link_code_ttl_minutes * 60, httponly=True,
                        secure=s.cookie_secure, samesite="lax", path="/api/auth")
    return challenge_instructions(ch, code)


@router.get("/whatsapp/status")
def whatsapp_login_status(id: str, request: Request, response: Response, db: Session = Depends(get_db)):
    ch = db.get(LoginChallenge, id)
    secret = request.cookies.get(LOGIN_COOKIE)
    if (ch is None or ch.purpose != "login" or not secret
            or sha256_hex(secret) != ch.poll_secret_hash):
        raise HTTPException(404, "Unknown login")
    if ch.status == "denied":
        raise HTTPException(403, "This account is unavailable")
    if ch.status == "consumed" or (ch.status == "pending" and login_svc.is_expired(ch)):
        return {"status": "expired"}
    if ch.status == "pending":
        return {"status": "pending"}
    user = db.get(User, ch.user_id)
    if user is None or user.status != "active":
        raise HTTPException(403, "This account is unavailable")
    # Single use, atomically: concurrent polls cannot both mint a session.
    won = db.execute(update(LoginChallenge).where(LoginChallenge.id == ch.id, LoginChallenge.status == "confirmed")
                     .values(status="consumed")).rowcount
    if won != 1:
        return {"status": "expired"}
    sess = _start_session(db, user, request, response)
    audit(db, "auth.login", actor_id=user.id, details={"method": "whatsapp"}, ip=client_ip(request))
    db.commit()
    response.delete_cookie(LOGIN_COOKIE, path="/api/auth")
    return {"status": "confirmed", "user": user_out(db, user), "csrf_token": sess.csrf_token}
