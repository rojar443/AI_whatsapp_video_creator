"""Deterministic guards that do not depend on any model output."""
import re
import unicodedata

_AFFIRM = (
    r"yes|y|yeah|yep|yup|ya|yes please|sure|ok|okay|k|confirm|confirmed|go|go ahead|do it|"
    r"generate|generate it|start|start it|create it|make it|looks good|look good|perfect|"
    r"sounds good|great|lgtm|approved|approve|proceed|haan|han|haa|ji haan|sari|shari|"
    r"ശരി|അതെ|हाँ|हां|ठीक है"
)
_CONFIRM_RE = re.compile(
    rf"^(?:{_AFFIRM})(?:[\s,]+(?:please|pls|thanks|thank you|now|go ahead|let'?s go|{_AFFIRM}))*$",
    re.IGNORECASE,
)


def normalize(text: str) -> str:
    text = unicodedata.normalize("NFKC", text or "").strip().lower()
    text = re.sub(r"[!.?👍✅🙌🔥😀-🙏\s]+$", "", text)
    return re.sub(r"\s+", " ", text)


def is_explicit_confirmation(text: str) -> bool:
    """True only for short, unambiguous approvals like 'yes', 'go ahead', 'ok please'.

    A model can classify intent, but spending credits requires this deterministic check
    (or a button reply carrying the current confirmation nonce). Long messages that merely
    contain 'yes' — e.g. "yes but make it 60 seconds" — are not confirmations.
    """
    t = normalize(text)
    if not t or len(t) > 40:
        return False
    return bool(_CONFIRM_RE.match(t))


_LINK_RE = re.compile(r"^\s*link\s*[:#-]?\s*(\d{6})\s*$", re.IGNORECASE)


def parse_link_code(text: str) -> str | None:
    m = _LINK_RE.match(text or "")
    return m.group(1) if m else None
