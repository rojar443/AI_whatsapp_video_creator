"""Messaging provider interface + parser for the WhatsApp Cloud API webhook format.

The dev provider speaks the same webhook format, so the whole inbound path (signature
check, parsing, idempotency) is exercised in development and tests.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any


@dataclass
class InboundMessage:
    wa_id: str
    message_id: str
    kind: str  # text | audio | interactive | unsupported
    timestamp: datetime
    text: str | None = None
    media_id: str | None = None
    media_mime: str | None = None
    button_id: str | None = None
    profile_name: str | None = None


@dataclass
class StatusUpdate:
    message_id: str
    status: str  # sent | delivered | read | failed
    recipient: str
    error: str | None = None


@dataclass
class ParsedWebhook:
    messages: list[InboundMessage] = field(default_factory=list)
    statuses: list[StatusUpdate] = field(default_factory=list)


class SendError(Exception):
    def __init__(self, message: str, retryable: bool = True, code: str | None = None):
        super().__init__(message)
        self.retryable = retryable
        self.code = code


class MessagingProvider(ABC):
    name = "base"
    requires_templates_outside_window = True
    supports_media_upload = True

    @abstractmethod
    def send_text(self, to: str, text: str) -> str: ...

    @abstractmethod
    def send_buttons(self, to: str, text: str, buttons: list[tuple[str, str]]) -> str: ...

    @abstractmethod
    def send_video(self, to: str, file_path: str, caption: str) -> str: ...

    @abstractmethod
    def send_template(self, to: str, template: str, language: str, params: list[str]) -> str: ...

    @abstractmethod
    def download_media(self, media_id: str) -> tuple[bytes, str]:
        """Return (bytes, mime_type)."""

    def health(self) -> dict[str, Any]:
        return {"ok": True, "provider": self.name}


def _ts(value: Any) -> datetime:
    try:
        return datetime.fromtimestamp(int(value), UTC)
    except (TypeError, ValueError):
        return datetime.now(UTC)


def parse_webhook(payload: dict[str, Any]) -> ParsedWebhook:
    """Parse a WhatsApp Cloud API webhook body. Unknown shapes are ignored, never raised."""
    out = ParsedWebhook()
    if not isinstance(payload, dict):
        return out
    for entry in payload.get("entry") or []:
        for change in (entry or {}).get("changes") or []:
            value = (change or {}).get("value") or {}
            names = {
                c.get("wa_id"): (c.get("profile") or {}).get("name")
                for c in value.get("contacts") or []
                if isinstance(c, dict)
            }
            for m in value.get("messages") or []:
                if not isinstance(m, dict) or not m.get("id") or not m.get("from"):
                    continue
                wa_id = "".join(ch for ch in str(m["from"]) if ch.isdigit())[:20]
                mtype = m.get("type")
                msg = InboundMessage(
                    wa_id=wa_id,
                    message_id=str(m["id"])[:128],
                    kind="unsupported",
                    timestamp=_ts(m.get("timestamp")),
                    profile_name=(names.get(m["from"]) or None),
                )
                if mtype == "text":
                    msg.kind = "text"
                    msg.text = str((m.get("text") or {}).get("body") or "")
                elif mtype in ("audio", "voice"):
                    audio = m.get("audio") or m.get("voice") or {}
                    msg.kind = "audio"
                    msg.media_id = audio.get("id")
                    msg.media_mime = audio.get("mime_type")
                elif mtype == "interactive":
                    inter = m.get("interactive") or {}
                    reply = inter.get("button_reply") or inter.get("list_reply") or {}
                    msg.kind = "interactive"
                    msg.button_id = str(reply.get("id") or "")[:128]
                    msg.text = str(reply.get("title") or "")
                elif mtype == "button":  # quick-reply on a template message
                    btn = m.get("button") or {}
                    msg.kind = "interactive"
                    msg.button_id = str(btn.get("payload") or "")[:128]
                    msg.text = str(btn.get("text") or "")
                out.messages.append(msg)
            for s in value.get("statuses") or []:
                if not isinstance(s, dict) or not s.get("id"):
                    continue
                err = None
                if s.get("errors"):
                    e0 = s["errors"][0]
                    err = f"{e0.get('code')}: {e0.get('title')}"[:500]
                out.statuses.append(
                    StatusUpdate(
                        message_id=str(s["id"]), status=str(s.get("status")),
                        recipient=str(s.get("recipient_id") or ""), error=err,
                    )
                )
    return out


def build_inbound_payload(
    wa_id: str, message_id: str, *, text: str | None = None, audio_media_id: str | None = None,
    audio_mime: str = "audio/ogg; codecs=opus", button_id: str | None = None,
    button_title: str = "", profile_name: str = "Tester", phone_number_id: str = "dev",
) -> dict[str, Any]:
    """Build a Cloud-API-shaped webhook body (used by the dev simulator and tests)."""
    import time

    msg: dict[str, Any] = {"from": wa_id, "id": message_id, "timestamp": str(int(time.time()))}
    if button_id:
        msg["type"] = "interactive"
        msg["interactive"] = {"type": "button_reply", "button_reply": {"id": button_id, "title": button_title}}
    elif audio_media_id:
        msg["type"] = "audio"
        msg["audio"] = {"id": audio_media_id, "mime_type": audio_mime, "voice": True}
    else:
        msg["type"] = "text"
        msg["text"] = {"body": text or ""}
    return {
        "object": "whatsapp_business_account",
        "entry": [{
            "id": "dev-waba",
            "changes": [{
                "field": "messages",
                "value": {
                    "messaging_product": "whatsapp",
                    "metadata": {"display_phone_number": "15550000000", "phone_number_id": phone_number_id},
                    "contacts": [{"profile": {"name": profile_name}, "wa_id": wa_id}],
                    "messages": [msg],
                },
            }],
        }],
    }
