"""Auth, CSRF and rate-limit dependencies."""
from __future__ import annotations

import threading
import time
from collections import defaultdict, deque
from datetime import timedelta

from fastapi import Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import get_db
from app.models import User, UserSession, aware, utcnow
from app.security import sha256_hex

SESSION_COOKIE = "rc_session"
CSRF_COOKIE = "rc_csrf"
CSRF_HEADER = "x-csrf-token"


def client_ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"


def _session_from_request(request: Request, db: Session) -> UserSession | None:
    token = request.cookies.get(SESSION_COOKIE)
    if not token:
        return None
    sess = db.scalar(select(UserSession).where(UserSession.token_hash == sha256_hex(token)))
    if sess is None or sess.revoked_at is not None or aware(sess.expires_at) < utcnow():
        return None
    return sess


def current_session(request: Request, db: Session = Depends(get_db)) -> UserSession:
    sess = _session_from_request(request, db)
    if sess is None:
        raise HTTPException(401, "not authenticated")
    # CSRF: unsafe methods must echo the per-session token in a header (double submit).
    if request.method not in ("GET", "HEAD", "OPTIONS"):
        if request.headers.get(CSRF_HEADER) != sess.csrf_token:
            raise HTTPException(403, "invalid CSRF token")
    user = db.get(User, sess.user_id)
    if user is None or user.status != "active":
        raise HTTPException(403, "account disabled")
    request.state.user = user
    return sess


def current_user(request: Request, sess: UserSession = Depends(current_session)) -> User:
    return request.state.user


def require_admin(user: User = Depends(current_user)) -> User:
    if user.role != "admin":
        raise HTTPException(403, "admin only")
    return user


def session_ttl() -> timedelta:
    return timedelta(hours=get_settings().session_ttl_hours)


class RateLimiter:
    """In-process sliding window. Fine for a single API instance; put Redis behind this
    interface (or rate-limit at the proxy) when running several replicas."""

    def __init__(self):
        self._hits: dict[str, deque] = defaultdict(deque)
        self._lock = threading.Lock()

    def hit(self, key: str, limit: int, window_seconds: int) -> bool:
        now = time.monotonic()
        with self._lock:
            q = self._hits[key]
            while q and q[0] <= now - window_seconds:
                q.popleft()
            if len(q) >= limit:
                return False
            q.append(now)
            return True

    def reset(self) -> None:
        with self._lock:
            self._hits.clear()


limiter = RateLimiter()


def rate_limit(name: str, limit: int, window_seconds: int):
    def dep(request: Request) -> None:
        if not limiter.hit(f"{name}:{client_ip(request)}", limit, window_seconds):
            raise HTTPException(429, "too many requests, please slow down")

    return dep
