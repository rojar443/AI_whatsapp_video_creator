"""Deterministic guards that do not depend on any model output."""
import re
import unicodedata

_AFFIRM = (
    r"yes|y|yeah|yep|yup|ya|yes please|sure|ok|okay|k|confirm|confirmed|go|go ahead|do it|proceed|"
    r"start|start it|make it|create it|render it|looks good|look good|perfect|sounds good|great|lgtm|"
    r"approved|approve|let'?s go|haan|han"
)
_CONFIRM_RE = re.compile(
    rf"^(?:{_AFFIRM})(?:[\s,]+(?:please|pls|thanks|thank you|now|go ahead|let'?s go|{_AFFIRM}))*$", re.I)
_DONE_RE = re.compile(
    r"\b(that'?s all|thats all|that is all|that'?s it|thats it|that'?s everything|all done|i'?m done|done|"
    r"finished|no more|nothing more|that'?s the lot|those are all)\b", re.I)


def normalize(text: str) -> str:
    text = unicodedata.normalize("NFKC", text or "").strip().lower().replace("’", "'")
    text = re.sub(r"[!.?👍✅🙌🔥😀-🙏\s]+$", "", text)
    return re.sub(r"\s+", " ", text)


def is_explicit_confirmation(text: str) -> bool:
    """True only for short, unambiguous approvals ('yes', 'go ahead', 'ok please').

    Starting a paid render requires this check or a button reply carrying the current
    plan nonce — a model saying intent=confirm is never enough. Longer messages that merely
    contain 'yes' ("yes but make it 60 seconds") are not confirmations.
    """
    t = normalize(text)
    if not t or len(t) > 40:
        return False
    return bool(_CONFIRM_RE.match(t))


def is_done_phrase(text: str) -> bool:
    t = normalize(text)
    if re.search(r"\b(is it|are you|when)\b.*\bdone\b", t):  # "is it done?" is a status question
        return False
    return bool(_DONE_RE.search(t))


_LINK_RE = re.compile(r"^\s*link\s*[:#-]?\s*(\d{6})\s*$", re.I)


def parse_link_code(text: str) -> str | None:
    m = _LINK_RE.match(text or "")
    return m.group(1) if m else None
