"""Telegram channel: update parsing, Bot API provider, polling ingest, full flow with a fake Telegram server."""
import json

import httpx
import pytest

from app import providers
from app.messaging.base import SendError
from app.messaging.router import MessagingRouter
from app.messaging.telegram import TelegramProvider, command_to_text, parse_update, to_html
from app.models import GeneratedAsset, GenerationJob, ProjectAsset, VideoProject, WhatsAppAccount
from app.security import sign_payload
from app.telegram_poller import handle_update
from tests.conftest import FAKE_MP4, drain, register, fresh_client

CHAT = 777000111


class FakeTelegram:
    """Minimal Telegram Bot API server behind httpx.MockTransport."""

    def __init__(self):
        self.calls: list[tuple[str, dict]] = []
        self.next_id = 100
        self.file_size = len(FAKE_MP4)

    def handler(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if request.method == "GET" and "/file/bot" in path:
            return httpx.Response(200, content=FAKE_MP4)
        method = path.rsplit("/", 1)[-1]
        ctype = request.headers.get("content-type", "")
        body = json.loads(request.content) if request.content and "json" in ctype else {"multipart": True}
        self.calls.append((method, body))
        if method == "getFile":
            if self.file_size > 20 * 1024 * 1024:
                return httpx.Response(400, json={"ok": False, "error_code": 400,
                                                 "description": "Bad Request: file is too big"})
            return httpx.Response(200, json={"ok": True, "result": {"file_id": body["file_id"],
                                                                    "file_size": self.file_size,
                                                                    "file_path": "videos/f.mp4"}})
        if method == "getMe":
            return httpx.Response(200, json={"ok": True, "result": {"username": "ReelChatTestBot"}})
        if method in ("sendMessage", "sendVideo"):
            self.next_id += 1
            return httpx.Response(200, json={"ok": True, "result": {"message_id": self.next_id}})
        return httpx.Response(200, json={"ok": True, "result": True})

    def sent(self, method="sendMessage") -> list[dict]:
        return [b for m, b in self.calls if m == method]

    def texts(self) -> list[str]:
        return [b.get("text", "") for b in self.sent()]


@pytest.fixture
def tg():
    fake = FakeTelegram()
    provider = TelegramProvider("TESTTOKEN", client=httpx.Client(transport=httpx.MockTransport(fake.handler)))
    providers._overrides["telegram"] = provider
    fake.provider = provider
    return fake


def msg(mid, **kw):
    return {"update_id": mid, "message": {"message_id": mid, "date": 1_790_000_000, "chat": {"id": CHAT, "type": "private"},
                                          "from": {"id": CHAT, "first_name": "Asha", "last_name": "N"}, **kw}}


def video(mid, caption=None):
    extra = {"caption": caption} if caption else {}
    return msg(mid, video={"file_id": f"vid{mid}", "mime_type": "video/mp4", "duration": 8, "width": 1920, "height": 1080},
               **extra)


def callback(cid, data):
    return {"update_id": 9000 + cid, "callback_query": {"id": str(cid), "from": {"id": CHAT, "first_name": "Asha"},
                                                        "data": data,
                                                        "message": {"message_id": 5, "chat": {"id": CHAT, "type": "private"}}}}


def feed(db, tg_fake, *updates):
    for u in updates:
        handle_update(db, tg_fake.provider, u)
    drain()


# ------------------------------------------------------------------ parsing
def test_parse_variants():
    m = parse_update(video(1, "cinematic travel reel"))
    assert (m.wa_id, m.kind, m.media_id, m.caption) == (f"tg:{CHAT}", "video", "tg:vid1", "cinematic travel reel")
    assert m.message_id == f"tg:{CHAT}:1" and m.profile_name == "Asha N"
    photo = parse_update(msg(2, photo=[{"file_id": "s", "width": 90, "height": 90},
                                       {"file_id": "big", "width": 1280, "height": 960}]))
    assert photo.kind == "image" and photo.media_id == "tg:big" and photo.media_mime == "image/jpeg"
    doc = parse_update(msg(3, document={"file_id": "d", "mime_type": "video/quicktime"}))
    assert doc.kind == "video" and doc.media_mime == "video/quicktime"
    assert parse_update(msg(4, document={"file_id": "d", "mime_type": "application/pdf"})).kind == "unsupported"
    voice = parse_update(msg(5, voice={"file_id": "v", "mime_type": "audio/ogg", "duration": 3}))
    assert voice.kind == "audio" and voice.media_id == "tg:v"
    cb = parse_update(callback(7, "confirm:p:n"))
    assert cb.kind == "interactive" and cb.button_id == "confirm:p:n" and cb.wa_id == f"tg:{CHAT}"


def test_groups_and_unknown_updates_ignored():
    group = msg(1, text="hi")
    group["message"]["chat"]["type"] = "supergroup"
    assert parse_update(group) is None
    assert parse_update({"update_id": 1, "edited_message": {}}) is None


def test_commands_and_formatting():
    assert command_to_text("/start") == "hi" and command_to_text("/help@ReelChatBot") == "help"
    assert command_to_text("/new") == "new" and command_to_text("plain text") == "plain text"
    assert parse_update(msg(1, text="/start link_abc.def")).text == "/start link_abc.def"
    assert to_html("Reply *yes* <now> & go") == "Reply <b>yes</b> &lt;now&gt; &amp; go"


# ------------------------------------------------------------------ provider
def test_provider_requests(tg, tmp_path):
    p = tg.provider
    assert p.send_text(f"tg:{CHAT}", "Hello *you*") == f"tg:{CHAT}:101"
    p.send_buttons(f"tg:{CHAT}", "Plan", [("confirm:p:n", "✅ Proceed"), ("cancel:p", "✖ Cancel")])
    kb = tg.sent()[1]["reply_markup"]["inline_keyboard"]
    assert kb[0][0] == {"text": "✅ Proceed", "callback_data": "confirm:p:n"} and len(kb) == 2
    assert tg.sent()[0] == {"chat_id": str(CHAT), "text": "Hello <b>you</b>", "parse_mode": "HTML"}
    f = tmp_path / "r.mp4"
    f.write_bytes(FAKE_MP4)
    p.send_video(f"tg:{CHAT}", str(f), "Your *reel*")
    assert tg.sent("sendVideo")
    dest = tmp_path / "dl"
    p.download_media("tg:vidX", str(dest), 10_000)
    assert dest.read_bytes() == FAKE_MP4


def test_provider_errors(tg, tmp_path):
    tg.file_size = 25 * 1024 * 1024
    with pytest.raises(SendError) as e:
        tg.provider.download_media("tg:big", str(tmp_path / "x"), 100 * 1024 * 1024)
    assert e.value.code == "too_large" and not e.value.retryable
    rate = TelegramProvider("T", client=httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(429, json={}))))
    with pytest.raises(SendError) as e:
        rate.send_text("tg:1", "x")
    assert e.value.retryable
    blocked = TelegramProvider("T", client=httpx.Client(transport=httpx.MockTransport(
        lambda r: httpx.Response(403, json={"ok": False, "error_code": 403, "description": "bot was blocked by the user"}))))
    with pytest.raises(SendError) as e:
        blocked.send_text("tg:1", "x")
    assert not e.value.retryable


def test_router_picks_channel(tg):
    from app.messaging.dev import DevMessagingProvider

    r = MessagingRouter(DevMessagingProvider(), tg.provider)
    assert r.for_to("tg:5") is tg.provider and r.for_to("15551234567").name == "dev"
    assert r.for_to("tg:5").requires_templates_outside_window is False
    with pytest.raises(RuntimeError):
        MessagingRouter(DevMessagingProvider(), None).for_to("tg:5")


# ------------------------------------------------------------------ flow
def test_full_flow_from_telegram_videos_to_reel(db, tg, fake_pipeline):
    feed(db, tg, msg(1, text="/start"), video(2), video(3), video(4))
    assert db.query(ProjectAsset).filter_by(status="ready").count() == 3  # downloaded via getFile
    feed(db, tg, msg(5, text="That's all. Make it a cinematic travel reel with a calm voiceover."))
    plans = [b for b in tg.sent() if "reply_markup" in b]
    assert plans and "Plan for your travel reel" in plans[-1]["text"]
    confirm = plans[-1]["reply_markup"]["inline_keyboard"][0][0]["callback_data"]
    assert confirm.startswith("confirm:") and len(confirm) <= 64
    assert not tg.sent("sendVideo")  # nothing rendered before confirmation

    feed(db, tg, msg(6, text="yes but make it longer"))  # not a confirmation
    assert db.query(GenerationJob).count() == 0

    confirm = [b for b in tg.sent() if "reply_markup" in b][-1]["reply_markup"]["inline_keyboard"][0][0]["callback_data"]
    feed(db, tg, callback(1, confirm), callback(1, confirm))  # double tap
    assert db.query(GenerationJob).one().status == "completed"
    assert len(tg.sent("sendVideo")) == 1  # delivered as a Telegram video, once
    assert any("being processed" in t for t in tg.texts())
    assert len(tg.sent("answerCallbackQuery")) == 2  # button spinners are cleared
    assert db.query(WhatsAppAccount).one().wa_id == f"tg:{CHAT}"


def test_unsupported_telegram_media_gets_reply(db, tg, fake_pipeline):
    feed(db, tg, msg(1, document={"file_id": "d", "mime_type": "application/pdf"}))
    assert any("videos, photos, text and voice notes" in t for t in tg.texts())


def test_link_telegram_chat_to_account_merges_draft(client, db, tg, fake_pipeline):
    feed(db, tg, video(2), video(3), video(4), msg(5, text="that's all, travel reel"))
    project = db.query(VideoProject).one()
    register(client, phone="")
    r = client.post("/api/me/telegram/link")
    assert r.status_code == 200 and r.json()["url"].startswith("https://t.me/ReelChatTestBot?start=link_")
    token = r.json()["url"].split("start=link_")[1]
    assert fresh_client().post("/api/me/telegram/link").status_code == 401

    forged = sign_payload({"uid": "someone-else"}, "telegram-link", 600)
    feed(db, tg, msg(6, text=f"/start link_{forged}"))
    assert any("didn't match" in t for t in tg.texts())
    feed(db, tg, msg(7, text="/start link_" + token))
    assert any("Linked to Asha" in t for t in tg.texts())
    me = client.get("/api/me").json()
    assert me["telegram"]["linked"] and me["whatsapp"]["status"] == "not_linked"
    items = client.get("/api/projects").json()["items"]
    assert [i["id"] for i in items] == [project.id]
    assert client.delete("/api/me/telegram").status_code == 200
    assert client.get("/api/me").json()["telegram"]["linked"] is False


def test_signup_without_phone(client):
    r = client.post("/api/auth/register", json={"name": "T", "email": "t@example.com", "password": "longenough1",
                                                "accept_terms": True})
    assert r.status_code == 201 and r.json()["user"]["phone"] is None
    client.headers["x-csrf-token"] = r.json()["csrf_token"]
    assert client.post("/api/me/whatsapp/link").status_code == 422  # needs a number first
