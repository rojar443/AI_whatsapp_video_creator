"""Development messaging provider: no network calls.

Outbound messages are only stored in the `messages` table (the website's dev simulator
shows them). Inbound voice notes uploaded through the simulator live in storage under
`dev-media/`. It enforces the same 24-hour window rule as the real API so template
fallbacks are exercised too.
"""
from __future__ import annotations

import uuid
from typing import Any

from app.messaging.base import MessagingProvider, SendError
from app.storage import get_storage


class DevMessagingProvider(MessagingProvider):
    name = "dev"
    requires_templates_outside_window = True
    supports_media_upload = True

    # Tests can set this to simulate transient delivery failures.
    fail_next: int = 0

    def _id(self) -> str:
        if DevMessagingProvider.fail_next > 0:
            DevMessagingProvider.fail_next -= 1
            raise SendError("simulated delivery failure", retryable=True)
        return f"wamid.dev.{uuid.uuid4().hex}"

    def send_text(self, to: str, text: str) -> str:
        return self._id()

    def send_buttons(self, to: str, text: str, buttons: list[tuple[str, str]]) -> str:
        return self._id()

    def send_video(self, to: str, file_path: str, caption: str) -> str:
        return self._id()

    def send_template(self, to: str, template: str, language: str, params: list[str]) -> str:
        return self._id()

    def download_media(self, media_id: str) -> tuple[bytes, str]:
        storage = get_storage()
        key = f"dev-media/{media_id}"
        if not storage.exists(key):
            raise SendError("media not found", retryable=False)
        data = storage.read_bytes(key)
        mime = storage.read_bytes(key + ".mime").decode() if storage.exists(key + ".mime") else "audio/ogg"
        return data, mime

    def health(self) -> dict[str, Any]:
        return {"ok": True, "provider": self.name, "note": "development provider — no real WhatsApp delivery"}
