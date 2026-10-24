"""Telegram Bot API channel (official API, long polling — no public URL needed locally).

Chats are stored with the id `tg:<chat_id>` in the same tables as WhatsApp numbers, so the
whole conversation engine, quotas and pipeline are shared. Only private chats are served.
"""
from __future__ import annotations

import html
import re
from datetime import UTC, datetime
from typing import Any

import httpx

from app.messaging.base import InboundMessage, MessagingProvider, SendError

API = "https://api.telegram.org"
PREFIX = "tg:"
MAX_TEXT = 4096


def is_telegram(ref: str | None) -> bool:
    return bool(ref) and str(ref).startswith(PREFIX)


def to_html(text: str) -> str:
    """Our reply templates use WhatsApp-style *bold*; Telegram needs HTML."""
    escaped = html.escape(text, quote=False)
    return re.sub(r"\*([^*\n]+)\*", r"<b>\1</b>", escaped)


def command_to_text(text: str, bot_username: str | None = None) -> str:
    """'/help@MyBot' → 'help'; '/start' → 'hi'. Other text is unchanged."""
    t = text.strip()
    if not t.startswith("/"):
        return t
    cmd, _, rest = t[1:].partition(" ")
    cmd = cmd.split("@", 1)[0].lower()
    if cmd == "start":
        return "hi"
    mapping = {"help": "help", "new": "new", "cancel": "cancel", "status": "status", "retry": "retry",
               "link": "link", "register": "register"}
    return mapping.get(cmd, t)


def parse_update(update: dict[str, Any]) -> InboundMessage | None:
    """Telegram update → InboundMessage (None for anything we don't serve)."""
    cb = update.get("callback_query")
    if isinstance(cb, dict):
        msg = cb.get("message") or {}
        chat = msg.get("chat") or {}
        if chat.get("type") != "private" or not cb.get("data"):
            return None
        return InboundMessage(
            wa_id=f"{PREFIX}{chat['id']}", message_id=f"{PREFIX}cb:{cb['id']}", kind="interactive",
            timestamp=datetime.now(UTC), text="", button_id=str(cb["data"])[:128],
            profile_name=_name(cb.get("from")))
    m = update.get("message")
    if not isinstance(m, dict):
        return None
    chat = m.get("chat") or {}
    if chat.get("type") != "private" or "id" not in chat:
        return None
    out = InboundMessage(
        wa_id=f"{PREFIX}{chat['id']}", message_id=f"{PREFIX}{chat['id']}:{m.get('message_id')}", kind="unsupported",
        timestamp=datetime.fromtimestamp(int(m.get("date") or 0), UTC) if m.get("date") else datetime.now(UTC),
        profile_name=_name(m.get("from")))
    caption = m.get("caption")
    if "text" in m:
        out.kind, out.text = "text", command_to_text(str(m["text"]))
        if str(m["text"]).startswith("/start link_"):
            out.text = str(m["text"]).strip()  # deep-link token, handled by the engine
    elif "video" in m:
        v = m["video"]
        out.kind, out.media_id, out.media_mime = "video", PREFIX + v["file_id"], v.get("mime_type") or "video/mp4"
    elif "photo" in m and m["photo"]:
        p = max(m["photo"], key=lambda s: (s.get("width") or 0) * (s.get("height") or 0))
        out.kind, out.media_id, out.media_mime = "image", PREFIX + p["file_id"], "image/jpeg"
    elif "document" in m:
        d = m["document"]
        mime = d.get("mime_type") or ""
        if mime.startswith("video/") or mime.startswith("image/"):  # "send as file" keeps full quality
            out.kind, out.media_id, out.media_mime = ("video" if mime.startswith("video/") else "image",
                                                      PREFIX + d["file_id"], mime)
    elif "voice" in m:
        out.kind, out.media_id, out.media_mime = "audio", PREFIX + m["voice"]["file_id"], \
            m["voice"].get("mime_type") or "audio/ogg"
    elif "audio" in m:
        out.kind, out.media_id, out.media_mime = "audio", PREFIX + m["audio"]["file_id"], \
            m["audio"].get("mime_type") or "audio/mpeg"
    elif "video_note" in m:
        out.kind, out.media_id, out.media_mime = "video", PREFIX + m["video_note"]["file_id"], "video/mp4"
    if caption and out.kind in ("video", "image"):
        out.caption = command_to_text(str(caption))[:1000]
    return out


def _name(user: dict | None) -> str | None:
    if not isinstance(user, dict):
        return None
    return " ".join(x for x in (user.get("first_name"), user.get("last_name")) if x)[:120] or None


class TelegramProvider(MessagingProvider):
    name = "telegram"
    requires_templates_outside_window = False  # no 24 h rule on Telegram
    supports_media_upload = True

    def __init__(self, token: str, client: httpx.Client | None = None, base: str = API):
        if not token:
            raise ValueError("TELEGRAM_BOT_TOKEN is required")
        self.token = token
        self.base = f"{base}/bot{token}"
        self.file_base = f"{base}/file/bot{token}"
        self.client = client or httpx.Client(timeout=40)

    # ------------------------------------------------------------------ plumbing
    def _call(self, method: str, **kwargs) -> Any:
        try:
            r = self.client.post(f"{self.base}/{method}", **kwargs)
        except httpx.HTTPError as e:
            raise SendError(f"telegram unreachable: {type(e).__name__}", retryable=True) from e
        try:
            data = r.json()
        except ValueError:
            data = {}
        if r.status_code == 429:
            raise SendError("telegram rate limit", retryable=True, code="429")
        if r.status_code >= 500:
            raise SendError(f"telegram error {r.status_code}", retryable=True)
        if not data.get("ok"):
            code = str(data.get("error_code") or r.status_code)
            desc = str(data.get("description") or r.text[:200])
            raise SendError(f"{code}: {desc}", retryable=False, code="too_large" if "too big" in desc else code)
        return data["result"]

    @staticmethod
    def _chat(to: str) -> str:
        return to.removeprefix(PREFIX)

    # ------------------------------------------------------------------ sending
    def send_text(self, to: str, text: str) -> str:
        res = None
        for i in range(0, len(text) or 1, MAX_TEXT):
            res = self._call("sendMessage", json={"chat_id": self._chat(to), "text": to_html(text[i:i + MAX_TEXT]),
                                                  "parse_mode": "HTML"})
        return f"{PREFIX}{self._chat(to)}:{res['message_id']}"

    def send_buttons(self, to: str, text: str, buttons: list[tuple[str, str]]) -> str:
        markup = {"inline_keyboard": [[{"text": title[:60], "callback_data": bid[:64]}] for bid, title in buttons]}
        res = self._call("sendMessage", json={"chat_id": self._chat(to), "text": to_html(text[:MAX_TEXT]),
                                              "parse_mode": "HTML", "reply_markup": markup})
        return f"{PREFIX}{self._chat(to)}:{res['message_id']}"

    def send_video(self, to: str, file_path: str, caption: str) -> str:
        with open(file_path, "rb") as fh:
            res = self._call("sendVideo", data={"chat_id": self._chat(to), "caption": to_html(caption)[:1024],
                                                "parse_mode": "HTML", "supports_streaming": "true"},
                             files={"video": ("reel.mp4", fh, "video/mp4")}, timeout=300)
        return f"{PREFIX}{self._chat(to)}:{res['message_id']}"

    def send_template(self, to: str, template: str, language: str, params: list[str]) -> str:
        return self.send_text(to, " ".join(params) or "Your reel is ready.")  # Telegram has no templates

    def answer_callback(self, callback_id: str) -> None:
        try:
            self._call("answerCallbackQuery", json={"callback_query_id": callback_id})
        except SendError:
            pass  # purely cosmetic (stops the button spinner)

    # ------------------------------------------------------------------ receiving
    def get_updates(self, offset: int | None, timeout: int = 25) -> list[dict]:
        params: dict[str, Any] = {"timeout": timeout, "allowed_updates": ["message", "callback_query"]}
        if offset is not None:
            params["offset"] = offset
        return self._call("getUpdates", json=params, timeout=timeout + 15)

    def download_media(self, media_id: str, dest_path: str, max_bytes: int) -> str:
        file_id = media_id.removeprefix(PREFIX)
        info = self._call("getFile", json={"file_id": file_id})  # fails with "file is too big" above 20 MB
        if int(info.get("file_size") or 0) > max_bytes:
            raise SendError("media too large", retryable=False, code="too_large")
        size = 0
        try:
            with self.client.stream("GET", f"{self.file_base}/{info['file_path']}", timeout=120) as r:
                r.raise_for_status()
                with open(dest_path, "wb") as fh:
                    for chunk in r.iter_bytes():
                        size += len(chunk)
                        if size > max_bytes:
                            raise SendError("media too large", retryable=False, code="too_large")
                        fh.write(chunk)
        except httpx.HTTPStatusError as e:
            raise SendError(f"download failed ({e.response.status_code})",
                            retryable=e.response.status_code >= 500) from e
        except httpx.HTTPError as e:
            raise SendError(f"download failed: {type(e).__name__}", retryable=True) from e
        return ""  # Telegram doesn't report a mime type; the message payload carries it

    def me(self) -> dict:
        return self._call("getMe")

    def health(self) -> dict[str, Any]:
        try:
            me = self.me()
            return {"ok": True, "provider": self.name, "bot": "@" + me.get("username", "")}
        except SendError as e:
            return {"ok": False, "provider": self.name, "error": str(e)[:200]}
