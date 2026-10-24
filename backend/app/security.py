"""Password hashing, opaque tokens, HMAC-signed tokens/URLs and webhook signatures."""
import base64
import hashlib
import hmac
import json
import re
import secrets
import time

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError

from app.config import get_settings

_ph = PasswordHasher()


def hash_password(password: str) -> str:
    return _ph.hash(password)


def verify_password(password_hash: str, password: str) -> bool:
    try:
        return _ph.verify(password_hash, password)
    except (VerificationError, InvalidHashError):
        return False


def new_token(nbytes: int = 32) -> str:
    return secrets.token_urlsafe(nbytes)


def sha256_hex(value: str | bytes) -> str:
    if isinstance(value, str):
        value = value.encode()
    return hashlib.sha256(value).hexdigest()


def _hmac(data: bytes, purpose: str) -> str:
    key = hashlib.sha256(f"{purpose}:{get_settings().secret_key}".encode()).digest()
    return base64.urlsafe_b64encode(hmac.new(key, data, hashlib.sha256).digest()).decode().rstrip("=")


def sign_payload(payload: dict, purpose: str, ttl_seconds: int) -> str:
    body = dict(payload, exp=int(time.time()) + ttl_seconds)
    raw = base64.urlsafe_b64encode(json.dumps(body, separators=(",", ":")).encode()).decode().rstrip("=")
    return f"{raw}.{_hmac(raw.encode(), purpose)}"


class TokenError(Exception):
    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason  # "invalid" | "expired"


def verify_payload(token: str, purpose: str) -> dict:
    try:
        raw, sig = token.rsplit(".", 1)
    except ValueError as e:
        raise TokenError("invalid") from e
    if not hmac.compare_digest(sig, _hmac(raw.encode(), purpose)):
        raise TokenError("invalid")
    try:
        body = json.loads(base64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4)))
    except ValueError as e:
        raise TokenError("invalid") from e
    if int(body.get("exp", 0)) < int(time.time()):
        raise TokenError("expired")
    return body


def sign_asset_url(asset_id: str, ttl_seconds: int | None = None) -> str:
    s = get_settings()
    ttl = ttl_seconds if ttl_seconds is not None else s.download_url_ttl_seconds
    exp = int(time.time()) + ttl
    sig = _hmac(f"{asset_id}:{exp}".encode(), "asset")
    return f"{s.public_base_url.rstrip('/')}/api/files/{asset_id}?exp={exp}&sig={sig}"


def verify_asset_signature(asset_id: str, exp: int, sig: str) -> str | None:
    """Returns None if valid, else 'invalid' or 'expired'."""
    if not hmac.compare_digest(sig, _hmac(f"{asset_id}:{exp}".encode(), "asset")):
        return "invalid"
    if exp < int(time.time()):
        return "expired"
    return None


def verify_meta_signature(raw_body: bytes, header_value: str | None, app_secret: str) -> bool:
    """Validates X-Hub-Signature-256 (HMAC-SHA256 of the raw body with the app secret)."""
    if not header_value or not header_value.startswith("sha256="):
        return False
    expected = hmac.new(app_secret.encode(), raw_body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(header_value.removeprefix("sha256="), expected)


def meta_signature(raw_body: bytes, app_secret: str) -> str:
    return "sha256=" + hmac.new(app_secret.encode(), raw_body, hashlib.sha256).hexdigest()


_PHONE_RE = re.compile(r"^\+?[1-9]\d{7,14}$")


def normalize_phone(phone: str) -> str | None:
    """Return E.164 ('+<digits>') or None. Accepts spaces, dashes and parentheses."""
    cleaned = re.sub(r"[\s\-().]", "", phone or "")
    if cleaned.startswith("00"):
        cleaned = "+" + cleaned[2:]
    if not _PHONE_RE.match(cleaned):
        return None
    return "+" + cleaned.lstrip("+")


def wa_id_from_e164(e164: str) -> str:
    return e164.lstrip("+")
