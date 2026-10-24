"""Telegram website login: one-time tokens carried by a t.me deep link or QR code.

The website asks for a token, the user presses Start in the bot (`/start login_<token>`), and the browser that
asked redeems it once with a secret that only it holds. Only hashes are stored."""
from __future__ import annotations

from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import LoginToken, aware, utcnow
from app.security import new_token, sha256_hex

PREFIX = "login_"


def create_token(db: Session) -> tuple[LoginToken, str, str]:
    """Returns (row, token, poll_secret). The token is 32 url-safe chars, so `login_<token>` fits Telegram's
    64-character start payload."""
    token, secret = new_token(24), new_token()
    row = LoginToken(token_hash=sha256_hex(token), poll_secret_hash=sha256_hex(secret),
                     expires_at=utcnow() + timedelta(minutes=get_settings().link_code_ttl_minutes))
    db.add(row)
    db.flush()
    return row, token, secret


def deep_link(token: str) -> str:
    return f"https://t.me/{get_settings().telegram_bot_username}?start={PREFIX}{token}"


def find_open(db: Session, token: str) -> LoginToken | None:
    row = db.scalar(select(LoginToken).where(LoginToken.token_hash == sha256_hex(token)))
    if row is None or row.status != "pending" or is_expired(row):
        return None
    return row


def is_expired(row: LoginToken) -> bool:
    return aware(row.expires_at) <= utcnow()
