"""Official WhatsApp Business Platform (Meta Cloud API) adapter."""
from __future__ import annotations

import mimetypes
from pathlib import Path
from typing import Any

import httpx

from app.messaging.base import MessagingProvider, SendError

GRAPH = "https://graph.facebook.com"


class MetaCloudProvider(MessagingProvider):
    name = "meta"
    requires_templates_outside_window = True
    supports_media_upload = True

    def __init__(self, access_token: str, phone_number_id: str, api_version: str = "v21.0",
                 timeout: float = 30.0, client: httpx.Client | None = None):
        if not access_token or not phone_number_id:
            raise ValueError("WHATSAPP_ACCESS_TOKEN and WHATSAPP_PHONE_NUMBER_ID are required")
        self.base = f"{GRAPH}/{api_version}"
        self.phone_number_id = phone_number_id
        self.client = client or httpx.Client(
            timeout=timeout, headers={"Authorization": f"Bearer {access_token}"}
        )

    def _post_message(self, body: dict[str, Any]) -> str:
        body = {"messaging_product": "whatsapp", "recipient_type": "individual", **body}
        try:
            r = self.client.post(f"{self.base}/{self.phone_number_id}/messages", json=body)
        except httpx.HTTPError as e:
            raise SendError(f"network error: {e}", retryable=True) from e
        if r.status_code >= 400:
            err = (r.json().get("error") if r.headers.get("content-type", "").startswith("application/json") else {}) or {}
            code = str(err.get("code") or r.status_code)
            # 131047 = outside the 24h window (needs template); 4xx other than 429 are not retryable.
            retryable = r.status_code == 429 or r.status_code >= 500
            raise SendError(f"{code}: {err.get('message') or r.text[:200]}", retryable=retryable, code=code)
        return r.json()["messages"][0]["id"]

    def send_text(self, to: str, text: str) -> str:
        return self._post_message({"to": to, "type": "text", "text": {"body": text[:4096], "preview_url": True}})

    def send_buttons(self, to: str, text: str, buttons: list[tuple[str, str]]) -> str:
        return self._post_message({
            "to": to,
            "type": "interactive",
            "interactive": {
                "type": "button",
                "body": {"text": text[:1024]},
                "action": {"buttons": [
                    {"type": "reply", "reply": {"id": bid[:256], "title": title[:20]}}
                    for bid, title in buttons[:3]
                ]},
            },
        })

    def upload_media(self, file_path: str) -> str:
        mime = mimetypes.guess_type(file_path)[0] or "video/mp4"
        with Path(file_path).open("rb") as fh:
            try:
                r = self.client.post(
                    f"{self.base}/{self.phone_number_id}/media",
                    data={"messaging_product": "whatsapp", "type": mime},
                    files={"file": (Path(file_path).name, fh, mime)},
                    timeout=120,
                )
            except httpx.HTTPError as e:
                raise SendError(f"upload failed: {e}") from e
        if r.status_code >= 400:
            raise SendError(f"upload failed: {r.text[:200]}", retryable=r.status_code >= 500)
        return r.json()["id"]

    def send_video(self, to: str, file_path: str, caption: str) -> str:
        media_id = self.upload_media(file_path)
        return self._post_message({"to": to, "type": "video", "video": {"id": media_id, "caption": caption[:1024]}})

    def send_template(self, to: str, template: str, language: str, params: list[str]) -> str:
        components = []
        if params:
            components.append({"type": "body", "parameters": [{"type": "text", "text": p[:1000]} for p in params]})
        return self._post_message({
            "to": to, "type": "template",
            "template": {"name": template, "language": {"code": language}, "components": components},
        })

    def download_media(self, media_id: str) -> tuple[bytes, str]:
        try:
            meta = self.client.get(f"{self.base}/{media_id}")
            meta.raise_for_status()
            info = meta.json()
            r = self.client.get(info["url"], timeout=60)
            r.raise_for_status()
        except (httpx.HTTPError, KeyError) as e:
            raise SendError(f"media download failed: {e}") from e
        return r.content, info.get("mime_type") or r.headers.get("content-type", "")

    def health(self) -> dict[str, Any]:
        try:
            r = self.client.get(f"{self.base}/{self.phone_number_id}", params={"fields": "display_phone_number"})
            return {"ok": r.status_code == 200, "provider": self.name, "status_code": r.status_code}
        except httpx.HTTPError as e:
            return {"ok": False, "provider": self.name, "error": str(e)[:200]}
