"""Transactional outbox for WhatsApp replies.

Handlers append outbound Message rows (status=pending) and one deliver task per turn in the
same DB transaction. The worker delivers them in order, applying WhatsApp policy:
outside the 24h customer-service window only an approved template may be sent.
"""
from __future__ import annotations

import logging
from datetime import timedelta
from pathlib import Path

from sqlalchemy.orm import Session

from app.config import get_settings
from app.jobs.queue import RetryableTaskError, enqueue
from app.messaging.base import SendError
from app.models import Conversation, GeneratedAsset, Message, aware, utcnow
from app.security import sign_asset_url
from app.storage import get_storage

log = logging.getLogger(__name__)
WINDOW = timedelta(hours=24)


class Outbox:
    def __init__(self, db: Session, conversation: Conversation, project_id: str | None = None):
        self.db = db
        self.conv = conversation
        self.project_id = project_id
        self.ids: list[str] = []

    def _add(self, kind: str, body: str, payload: dict | None = None) -> Message:
        m = Message(conversation_id=self.conv.id, user_id=self.conv.user_id, project_id=self.project_id,
                    direction="outbound", kind=kind, body=body, payload=payload, status="pending")
        self.db.add(m)
        self.db.flush()
        self.ids.append(m.id)
        return m

    def text(self, body: str) -> Message:
        return self._add("text", body)

    def buttons(self, body: str, buttons: list[tuple[str, str]]) -> Message:
        return self._add("interactive", body, {"buttons": [{"id": i, "title": t} for i, t in buttons]})

    def video(self, asset: GeneratedAsset, caption: str, link_text: str, after_text: str = "") -> Message:
        return self._add("video", caption, {"asset_id": asset.id, "link_text": link_text,
                                            "after_text": after_text})

    def flush(self) -> None:
        if self.ids:
            enqueue(self.db, "deliver", {"message_ids": list(self.ids)},
                    dedupe_key=f"deliver:{self.ids[0]}", max_attempts=5)
            self.ids = []


def _in_window(conv: Conversation) -> bool:
    last = aware(conv.whatsapp_account.last_inbound_at)
    return bool(last and utcnow() - last < WINDOW)


def deliver_message(db: Session, msg: Message, provider) -> None:
    s = get_settings()
    conv = db.get(Conversation, msg.conversation_id)
    to = conv.whatsapp_account.wa_id
    payload = msg.payload or {}
    if not _in_window(conv) and provider.requires_templates_outside_window:
        text = msg.body or ""
        if msg.kind == "video":
            asset = db.get(GeneratedAsset, payload.get("asset_id"))
            text = payload.get("link_text", "") + " " + (sign_asset_url(asset.id) if asset else "")
        msg.channel_message_id = provider.send_template(
            to, s.whatsapp_notify_template, s.whatsapp_template_language, [text[:900]])
        msg.payload = dict(payload, via_template=True)
        return
    if msg.kind == "interactive":
        buttons = [(b["id"], b["title"]) for b in payload.get("buttons", [])]
        msg.channel_message_id = provider.send_buttons(to, msg.body or "", buttons)
    elif msg.kind == "video":
        asset = db.get(GeneratedAsset, payload.get("asset_id"))
        if asset is None or asset.deleted_at:
            raise SendError("asset missing", retryable=False)
        url = sign_asset_url(asset.id)
        msg.payload = dict(payload, url=url)
        if provider.supports_media_upload and asset.size_bytes <= s.whatsapp_max_video_bytes:
            workdir = Path(s.work_dir) / "deliver"
            workdir.mkdir(parents=True, exist_ok=True)
            local = get_storage().local_path(asset.storage_key, str(workdir))
            msg.channel_message_id = provider.send_video(to, local, msg.body or "")
            msg.payload = dict(msg.payload, delivered_as="video")
        else:
            body = f"{payload.get('link_text', '')}\n{url}\n\n{payload.get('after_text', '')}".strip()
            msg.channel_message_id = provider.send_text(to, body)
            msg.kind = "link"
            msg.payload = dict(msg.payload, delivered_as="link")
    else:
        msg.channel_message_id = provider.send_text(to, msg.body or "")


def deliver_batch(db: Session, message_ids: list[str], provider) -> None:
    """Deliver in order; stop at the first retryable failure so ordering is preserved."""
    for mid in message_ids:
        msg = db.get(Message, mid)
        if msg is None or msg.status in ("sent", "delivered", "read"):
            continue
        try:
            deliver_message(db, msg, provider)
            msg.status = "sent"
            msg.error = None
            db.commit()
        except SendError as e:
            msg.error = str(e)[:500]
            if e.retryable:
                msg.status = "pending"
                db.commit()
                raise RetryableTaskError(f"delivery failed: {e}") from e
            msg.status = "failed"
            db.commit()
            log.warning("message %s permanently failed: %s", mid, e)
