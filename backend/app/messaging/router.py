"""Routes by channel: `tg:` ids / file ids go to Telegram, everything else to WhatsApp (or dev)."""
from __future__ import annotations

from typing import Any

from app.messaging.base import MessagingProvider
from app.messaging.telegram import PREFIX, TelegramProvider


class MessagingRouter(MessagingProvider):
    name = "router"

    def __init__(self, primary: MessagingProvider, telegram: TelegramProvider | None):
        self.primary, self.telegram = primary, telegram

    def for_to(self, ref: str) -> MessagingProvider:
        if ref.startswith(PREFIX):
            if self.telegram is None:
                raise RuntimeError("Telegram is not configured (TELEGRAM_BOT_TOKEN)")
            return self.telegram
        return self.primary

    @property
    def requires_templates_outside_window(self) -> bool:  # type: ignore[override]
        return self.primary.requires_templates_outside_window

    def send_text(self, to, text): return self.for_to(to).send_text(to, text)
    def send_buttons(self, to, text, buttons): return self.for_to(to).send_buttons(to, text, buttons)
    def send_video(self, to, file_path, caption): return self.for_to(to).send_video(to, file_path, caption)
    def send_template(self, to, template, language, params): return self.for_to(to).send_template(to, template, language, params)
    def download_media(self, media_id, dest_path, max_bytes): return self.for_to(media_id).download_media(media_id, dest_path, max_bytes)

    def health(self) -> dict[str, Any]:
        out = self.primary.health()
        if self.telegram:
            out["telegram"] = self.telegram.health()
        return out
