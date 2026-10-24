"""WhatsApp login / re-authentication challenges.

A visitor requests a code on the website and proves they control the number by sending
`LOGIN <code>` to the business number. The sender's number comes from the signed webhook, so a
code sent from another number never matches. The browser that requested the code redeems it once
with a secret that only it holds."""
from __future__ import annotations

import secrets
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import LoginChallenge, User, aware, utcnow
from app.security import new_token, sha256_hex

MAX_ATTEMPTS = 5


def code_hash(wa_id: str, code: str) -> str:
    return sha256_hex(f"login:{wa_id}:{code}:{get_settings().secret_key}")


def create_challenge(db: Session, wa_id: str, purpose: str = "login", user: User | None = None,
                     terms_accepted: bool = False) -> tuple[LoginChallenge, str, str]:
    """Returns (challenge, code, poll_secret); only hashes are stored."""
    code = f"{secrets.randbelow(1_000_000):06d}"
    poll_secret = new_token()
    ch = LoginChallenge(purpose=purpose, wa_id=wa_id, code_hash=code_hash(wa_id, code),
                        poll_secret_hash=sha256_hex(poll_secret), user_id=user.id if user else None,
                        terms_accepted_at=utcnow() if terms_accepted else None,
                        expires_at=utcnow() + timedelta(minutes=get_settings().link_code_ttl_minutes))
    db.add(ch)
    db.flush()
    return ch, code, poll_secret


def is_expired(ch: LoginChallenge) -> bool:
    return aware(ch.expires_at) <= utcnow()


def open_challenges(db: Session, wa_id: str) -> list[LoginChallenge]:
    rows = db.scalars(select(LoginChallenge).where(
        LoginChallenge.wa_id == wa_id, LoginChallenge.status == "pending",
        LoginChallenge.expires_at > utcnow()).order_by(LoginChallenge.created_at.desc())).all()
    return list(rows)


def match_code(db: Session, wa_id: str, code: str) -> LoginChallenge | None:
    """The open challenge for this number that `code` belongs to. A wrong code counts against every
    open challenge of the number, so the 6-digit space cannot be brute-forced."""
    rows = open_challenges(db, wa_id)
    want = code_hash(wa_id, code)
    for ch in rows:
        if ch.attempts < MAX_ATTEMPTS and secrets.compare_digest(ch.code_hash, want):
            return ch
    for ch in rows:
        ch.attempts += 1
    return None


def redeem_reauth(db: Session, user: User, challenge_id: str | None) -> bool:
    """Single use: a confirmed re-authentication challenge of this user proves a fresh WhatsApp check."""
    if not challenge_id:
        return False
    ch = db.get(LoginChallenge, challenge_id)
    if (ch is None or ch.purpose != "reauth" or ch.user_id != user.id or ch.status != "confirmed"
            or is_expired(ch)):
        return False
    ch.status = "consumed"
    db.flush()
    return True
