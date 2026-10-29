"""Rejection and race branches in auth deps, token signing, phone parsing and webhook ingestion."""
import json
from datetime import timedelta

import pytest
from sqlalchemy import select

from app.api import deps
from app.api.webhooks import get_or_create_conversation, ingest_message
from app.messaging.base import InboundMessage
from app.models import Conversation, Message, User, UserSession, utcnow
from app.security import TokenError, _hmac, normalize_phone, sign_payload, verify_payload
from tests.conftest import register


# --------------------------------------------------------------------------- sessions
def test_revoked_session_is_rejected(client, db):
    register(client)
    assert client.get("/api/me").status_code == 200
    sess = db.scalar(select(UserSession))
    sess.revoked_at = utcnow()
    db.commit()
    assert client.get("/api/me").status_code == 401


def test_expired_session_is_rejected(client, db):
    register(client)
    sess = db.scalar(select(UserSession))
    sess.expires_at = utcnow() - timedelta(seconds=1)
    db.commit()
    assert client.get("/api/me").status_code == 401


def test_unknown_session_token_is_rejected(client):
    client.cookies.set(deps.SESSION_COOKIE, "not-a-real-token")
    assert client.get("/api/me").status_code == 401


def test_suspended_account_is_blocked_even_with_valid_session(client, db):
    register(client)
    db.scalar(select(User).where(User.email == "asha@example.com")).status = "suspended"
    db.commit()
    r = client.get("/api/me")
    assert r.status_code == 403
    assert r.json()["detail"] == "account disabled"


def test_csrf_checked_before_account_status(client, db):
    register(client)
    del client.headers["x-csrf-token"]
    r = client.post("/api/auth/logout")
    assert r.status_code == 403
    assert r.json()["detail"] == "invalid CSRF token"


# --------------------------------------------------------------------------- rate limiter
def test_rate_limiter_window_slides(monkeypatch):
    now = [1000.0]
    monkeypatch.setattr(deps.time, "monotonic", lambda: now[0])
    rl = deps.RateLimiter()
    assert rl.hit("k", 2, 60) and rl.hit("k", 2, 60)
    assert not rl.hit("k", 2, 60)
    now[0] += 59
    assert not rl.hit("k", 2, 60)
    now[0] += 1  # both earlier hits fall out of the window
    assert rl.hit("k", 2, 60)
    assert rl.hit("k", 2, 60)
    assert not rl.hit("k", 2, 60)
    assert rl.hit("other", 2, 60)  # keys are independent


# --------------------------------------------------------------------------- signed tokens
def test_token_round_trip_and_purpose_binding():
    t = sign_payload({"u": "1"}, "download", 60)
    assert verify_payload(t, "download")["u"] == "1"
    with pytest.raises(TokenError, match="invalid"):
        verify_payload(t, "email")


@pytest.mark.parametrize("token", ["no-dot-here", "", "abc.wrongsig"])
def test_malformed_or_unsigned_tokens(token):
    with pytest.raises(TokenError, match="invalid"):
        verify_payload(token, "download")


@pytest.mark.parametrize("raw", ["bm90LWpzb24", "@@@@"])  # validly signed, but not base64 JSON
def test_signed_garbage_body_is_invalid(raw):
    with pytest.raises(TokenError, match="invalid"):
        verify_payload(f"{raw}.{_hmac(raw.encode(), 'download')}", "download")


def test_expired_token():
    with pytest.raises(TokenError, match="expired"):
        verify_payload(sign_payload({"u": "1"}, "download", -5), "download")


def test_token_without_exp_counts_as_expired():
    import base64
    raw = base64.urlsafe_b64encode(json.dumps({"u": "1"}).encode()).decode().rstrip("=")
    with pytest.raises(TokenError, match="expired"):
        verify_payload(f"{raw}.{_hmac(raw.encode(), 'x')}", "x")


# --------------------------------------------------------------------------- phones
@pytest.mark.parametrize("given,want", [
    ("0044 20 7946 0958", "+442079460958"),
    ("00 1 (555) 765-4321", "+15557654321"),
    ("+1.555.765.4321", "+15557654321"),
    ("15557654321", "+15557654321"),
    ("0012", None),
    ("+0123456789", None),
    ("1234567", None),
    ("1" * 16, None),
    (None, None),
])
def test_normalize_phone(given, want):
    assert normalize_phone(given) == want


# --------------------------------------------------------------------------- webhook races
def _msg(mid="wamid.1", wa_id="15550001111"):
    return InboundMessage(wa_id=wa_id, message_id=mid, kind="text", timestamp=utcnow(), text="hi", profile_name="Ravi")


def _hide_first(db, monkeypatch, model):
    """Make the first `select` of `model` miss, as if another worker inserted it right after we looked."""
    real, seen = db.scalar, {"n": 0}

    def scalar(stmt, *a, **kw):
        entity = stmt.column_descriptions[0].get("entity")
        if entity is model and seen["n"] == 0:
            seen["n"] += 1
            return None
        return real(stmt, *a, **kw)
    monkeypatch.setattr(db, "scalar", scalar)
    return seen


def test_conversation_created_concurrently_is_reused(db, monkeypatch):
    first = get_or_create_conversation(db, "15550001111", "Ravi")
    db.commit()
    seen = _hide_first(db, monkeypatch, Conversation)
    again = get_or_create_conversation(db, "15550001111", "Ravi")
    assert seen["n"] == 1
    assert again.id == first.id
    assert db.scalar(select(Conversation).where(Conversation.id == first.id)) is not None
    assert len(db.scalars(select(Conversation)).all()) == 1


def test_concurrent_duplicate_delivery_is_dropped(db, monkeypatch):
    assert ingest_message(db, _msg()) is not None
    db.commit()
    real = db.scalar

    def scalar(stmt, *a, **kw):  # the duplicate pre-check misses (the other worker hadn't committed yet)
        if stmt.column_descriptions[0].get("name") == "id" and "channel_message_id" in str(stmt):
            return None
        return real(stmt, *a, **kw)
    monkeypatch.setattr(db, "scalar", scalar)
    assert ingest_message(db, _msg()) is None
    db.commit()
    assert len(db.scalars(select(Message)).all()) == 1


def test_sequential_duplicate_is_dropped_without_touching_db(db):
    assert ingest_message(db, _msg()) is not None
    db.commit()
    assert ingest_message(db, _msg()) is None
