"""Messaging layer: Telegram adapter, webhook parsing edge cases, routing, dev provider and outbound delivery."""
import json
from datetime import timedelta

import httpx
import pytest

from app.config import get_settings
from app.jobs.dispatch import RetryableError
from app.messaging import telegram as T
from app.messaging.base import MessagingProvider, SendError, _ts, parse_webhook
from app.messaging.dev import DevMessagingProvider
from app.messaging.router import MessagingRouter
from app.models import Conversation, GeneratedAsset, Message, User, WhatsAppAccount, utcnow
from app.services import outbox
from app.storage import get_storage


# ------------------------------------------------------------------ Telegram helpers
@pytest.mark.parametrize("ref,ok", [("tg:1", True), ("1555", False), (None, False), ("", False)])
def test_is_telegram(ref, ok):
    assert T.is_telegram(ref) is ok


def test_to_html_escapes_and_bolds():
    assert T.to_html("*Hi* <b> & *x*") == "<b>Hi</b> &lt;b&gt; &amp; <b>x</b>"
    assert T.to_html("a*b\nc*") == "a*b\nc*"


@pytest.mark.parametrize("text,expected", [
    ("/start", "hi"), ("/START foo", "hi"), ("/help@ReelBot", "help"), ("/new", "new"), ("/link", "link"),
    ("/unknown arg", "/unknown arg"), ("  plain text ", "plain text"),
])
def test_command_to_text(text, expected):
    assert T.command_to_text(text) == expected


def msg(**kw):
    base = {"message_id": 5, "date": 1700000000, "chat": {"id": 9, "type": "private"},
            "from": {"first_name": "Asha", "last_name": "Nair"}}
    base.update(kw)
    return {"update_id": 1, "message": base}


def test_parse_update_text_and_deeplinks():
    m = T.parse_update(msg(text="/help"))
    assert (m.wa_id, m.message_id, m.kind, m.text, m.profile_name) == ("tg:9", "tg:9:5", "text", "help", "Asha Nair")
    assert T.parse_update(msg(text="/start login_abc ")).text == "/start login_abc"
    assert T.parse_update(msg(text="/start link_xyz")).text == "/start link_xyz"


def test_parse_update_media_kinds():
    v = T.parse_update(msg(video={"file_id": "V"}, caption="/new trip"))
    assert (v.kind, v.media_id, v.media_mime, v.caption) == ("video", "tg:V", "video/mp4", "new")
    ph = T.parse_update(msg(photo=[{"file_id": "s", "width": 10, "height": 10}, {"file_id": "L", "width": 99, "height": 99}]))
    assert (ph.kind, ph.media_id) == ("image", "tg:L")
    doc = T.parse_update(msg(document={"file_id": "D", "mime_type": "image/png"}))
    assert (doc.kind, doc.media_mime) == ("image", "image/png")
    dv = T.parse_update(msg(document={"file_id": "D", "mime_type": "video/quicktime"}))
    assert dv.kind == "video"
    assert T.parse_update(msg(document={"file_id": "D", "mime_type": "application/pdf"})).kind == "unsupported"
    vo = T.parse_update(msg(voice={"file_id": "A"}))
    assert (vo.kind, vo.media_mime) == ("audio", "audio/ogg")
    au = T.parse_update(msg(audio={"file_id": "B"}))
    assert (au.kind, au.media_mime) == ("audio", "audio/mpeg")
    vn = T.parse_update(msg(video_note={"file_id": "C"}))
    assert (vn.kind, vn.media_id) == ("video", "tg:C")
    st = T.parse_update(msg(sticker={"file_id": "S"}, caption="ignored"))
    assert st.kind == "unsupported" and st.caption is None


def test_parse_update_rejects():
    assert T.parse_update({"update_id": 1}) is None
    assert T.parse_update({"message": "x"}) is None
    assert T.parse_update(msg(chat={"id": 1, "type": "group"})) is None
    assert T.parse_update(msg(chat={"type": "private"})) is None
    assert T.parse_update(msg(photo=[])).kind == "unsupported"


def test_parse_update_no_date_and_no_name():
    m = T.parse_update(msg(date=None, text="x", **{"from": None}))
    assert m.profile_name is None and m.timestamp is not None
    assert T._name({}) is None and T._name("x") is None


def test_parse_callback():
    cb = {"callback_query": {"id": "C1", "data": "confirm:p:n", "from": {"first_name": "A"},
                             "message": {"chat": {"id": 3, "type": "private"}}}}
    m = T.parse_update(cb)
    assert (m.kind, m.button_id, m.message_id, m.wa_id) == ("interactive", "confirm:p:n", "tg:cb:C1", "tg:3")
    cb["callback_query"]["message"]["chat"]["type"] = "group"
    assert T.parse_update(cb) is None


# ------------------------------------------------------------------ Telegram provider
def tg(handler):
    sent = []

    def wrap(req):
        sent.append(req)
        return handler(req)

    return T.TelegramProvider("123:ABC", client=httpx.Client(transport=httpx.MockTransport(wrap)),
                              base="https://tg.test"), sent


def ok(result):
    return lambda r: httpx.Response(200, json={"ok": True, "result": result})


def test_telegram_requires_token():
    with pytest.raises(ValueError):
        T.TelegramProvider("")
    p = T.TelegramProvider("t")
    assert p.base == "https://api.telegram.org/bott" and p.file_base.endswith("/file/bott")


def test_send_text_splits_long_messages():
    p, sent = tg(ok({"message_id": 7}))
    assert p.send_text("tg:9", "a" * 5000) == "tg:9:7"
    assert len(sent) == 2 and str(sent[0].url) == "https://tg.test/bot123:ABC/sendMessage"
    b = json.loads(sent[0].content)
    assert b["chat_id"] == "9" and b["parse_mode"] == "HTML" and len(b["text"]) == 4096
    p.send_text("tg:9", "")
    assert len(sent) == 3


def test_send_buttons_markup():
    p, sent = tg(ok({"message_id": 1}))
    p.send_buttons("tg:9", "Pick", [("x" * 100, "t" * 100), ("b", "B")])
    kb = json.loads(sent[0].content)["reply_markup"]["inline_keyboard"]
    assert len(kb) == 2 and len(kb[0][0]["callback_data"]) == 64 and len(kb[0][0]["text"]) == 60


def test_send_video(tmp_path):
    f = tmp_path / "r.mp4"
    f.write_bytes(b"MP4")
    p, sent = tg(ok({"message_id": 2}))
    assert p.send_video("tg:9", str(f), "*Done*") == "tg:9:2"
    assert b"<b>Done</b>" in sent[0].content and b"supports_streaming" in sent[0].content


def test_send_template_falls_back_to_text():
    p, sent = tg(ok({"message_id": 3}))
    p.send_template("tg:9", "tpl", "en", ["Your", "reel"])
    assert json.loads(sent[0].content)["text"] == "Your reel"
    p.send_template("tg:9", "tpl", "en", [])
    assert json.loads(sent[1].content)["text"] == "Your reel is ready."


@pytest.mark.parametrize("resp,retryable,code", [
    (httpx.Response(429, json={"ok": False}), True, "429"),
    (httpx.Response(502, text="bad gateway"), True, None),
    (httpx.Response(400, json={"ok": False, "error_code": 400, "description": "chat not found"}), False, "400"),
    (httpx.Response(400, json={"ok": False, "description": "file is too big"}), False, "too_large"),
    (httpx.Response(200, text="not json"), False, "200"),
])
def test_call_errors(resp, retryable, code):
    p, _ = tg(lambda r: resp)
    with pytest.raises(SendError) as ei:
        p.send_text("tg:1", "x")
    assert ei.value.retryable is retryable
    if code:
        assert ei.value.code == code


def test_call_network_error():
    def boom(r):
        raise httpx.ConnectError("x")

    p, _ = tg(boom)
    with pytest.raises(SendError, match="unreachable: ConnectError") as ei:
        p.me()
    assert ei.value.retryable


def test_answer_callback_swallows_errors():
    p, sent = tg(lambda r: httpx.Response(400, json={"ok": False}))
    p.answer_callback("cb")
    assert json.loads(sent[0].content) == {"callback_query_id": "cb"}


def test_get_updates_params():
    p, sent = tg(ok([{"update_id": 1}]))
    assert p.get_updates(None, 5) == [{"update_id": 1}]
    assert "offset" not in json.loads(sent[0].content)
    p.get_updates(42)
    b = json.loads(sent[1].content)
    assert b["offset"] == 42 and b["timeout"] == 25 and b["allowed_updates"] == ["message", "callback_query"]


def file_handler(info, payload=b"data", status=200, exc=None):
    def h(req):
        if "/file/" in req.url.path:
            if exc:
                raise exc
            return httpx.Response(status, content=payload)
        return httpx.Response(200, json={"ok": True, "result": info})
    return h


def test_download_media(tmp_path):
    p, sent = tg(file_handler({"file_path": "videos/a.mp4", "file_size": 4}))
    dest = tmp_path / "x"
    assert p.download_media("tg:FID", str(dest), 10) == ""
    assert dest.read_bytes() == b"data" and json.loads(sent[0].content) == {"file_id": "FID"}
    assert sent[1].url.path == "/file/bot123:ABC/videos/a.mp4"


def test_download_media_too_large(tmp_path):
    p, _ = tg(file_handler({"file_path": "a", "file_size": 99}))
    with pytest.raises(SendError) as ei:
        p.download_media("tg:F", str(tmp_path / "x"), 10)
    assert ei.value.code == "too_large"
    p2, _ = tg(file_handler({"file_path": "a"}, payload=b"x" * 50))
    with pytest.raises(SendError) as ei:
        p2.download_media("tg:F", str(tmp_path / "x"), 10)
    assert ei.value.code == "too_large"


@pytest.mark.parametrize("status,retryable", [(404, False), (500, True)])
def test_download_media_http_status(tmp_path, status, retryable):
    p, _ = tg(file_handler({"file_path": "a"}, status=status))
    with pytest.raises(SendError) as ei:
        p.download_media("tg:F", str(tmp_path / "x"), 10)
    assert ei.value.retryable is retryable


def test_download_media_network_error(tmp_path):
    p, _ = tg(file_handler({"file_path": "a"}, exc=httpx.ReadTimeout("t")))
    with pytest.raises(SendError, match="ReadTimeout") as ei:
        p.download_media("tg:F", str(tmp_path / "x"), 10)
    assert ei.value.retryable


def test_telegram_health():
    p, _ = tg(ok({"username": "reelbot"}))
    assert p.health() == {"ok": True, "provider": "telegram", "bot": "@reelbot"}
    p2, _ = tg(lambda r: httpx.Response(401, json={"ok": False, "error_code": 401, "description": "Unauthorized"}))
    assert p2.health()["ok"] is False and "Unauthorized" in p2.health()["error"]


# ------------------------------------------------------------------ WhatsApp webhook parsing edge cases
def test_ts_fallback():
    assert _ts("1700000000").year == 2023
    assert _ts("garbage") is not None and _ts(None) is not None


@pytest.mark.parametrize("payload", [None, [], "x", {"entry": "x"}, {"entry": [1]}, {"entry": [{"changes": "x"}]},
                                     {"entry": [{"changes": [{"value": "x"}]}]}, {"entry": [{"changes": [1]}]}])
def test_parse_webhook_ignores_garbage(payload):
    out = parse_webhook(payload)
    assert out.messages == [] and out.statuses == []


def wrap_value(value):
    return {"entry": [{"changes": [{"value": value}]}]}


def test_parse_webhook_message_types():
    msgs = [
        {"from": "+1 555", "id": "a", "type": "voice", "voice": {"id": "V", "mime_type": "audio/ogg"}},
        {"from": "1", "id": "b", "type": "document", "document": {"id": "D", "mime_type": "video/mp4", "caption": "c"}},
        {"from": "1", "id": "c", "type": "document", "document": {"id": "D2", "mime_type": "application/pdf"}},
        {"from": "1", "id": "d", "type": "interactive", "interactive": {"list_reply": {"id": "L", "title": "T"}}},
        {"from": "1", "id": "e", "type": "button", "button": {"payload": "P", "text": "Yes"}},
        {"from": "1", "id": "f", "type": "location"},
        {"id": "nofrom"}, "notadict",
    ]
    out = parse_webhook(wrap_value({"messages": msgs, "contacts": [{"wa_id": "+1 555", "profile": {"name": "Z"}}, 1]}))
    by = {m.message_id: m for m in out.messages}
    assert by["a"].kind == "audio" and by["a"].wa_id == "1555" and by["a"].profile_name == "Z"
    assert by["b"].kind == "video" and by["b"].caption == "c"
    assert by["c"].kind == "unsupported"
    assert (by["d"].button_id, by["d"].text) == ("L", "T")
    assert (by["e"].kind, by["e"].button_id, by["e"].text) == ("interactive", "P", "Yes")
    assert by["f"].kind == "unsupported" and len(out.messages) == 6


def test_parse_webhook_statuses():
    out = parse_webhook(wrap_value({"statuses": [
        {"id": "s1", "status": "failed", "recipient_id": "1", "errors": [{"code": 131047, "title": "Re-engagement"}]},
        {"id": "s2", "status": "read"}, {"status": "x"}, 5]}))
    assert [(s.message_id, s.status, s.error) for s in out.statuses] == [
        ("s1", "failed", "131047: Re-engagement"), ("s2", "read", None)]


def test_base_health_default():
    class P(MessagingProvider):
        name = "p"
        send_text = send_buttons = send_video = send_template = download_media = lambda *a: None

    assert P().health() == {"ok": True, "provider": "p"}


# ------------------------------------------------------------------ router
class Stub:
    requires_templates_outside_window = True

    def __init__(self, name):
        self.name, self.calls = name, []

    def __getattr__(self, attr):
        return lambda *a: self.calls.append((attr, a)) or f"{self.name}:{attr}"

    def health(self):
        return {"ok": True, "provider": self.name}


def test_router_routes_by_prefix():
    wa, t = Stub("wa"), Stub("tg")
    r = MessagingRouter(wa, t)
    assert r.send_text("1555", "x") == "wa:send_text"
    assert r.send_buttons("tg:1", "x", []) == "tg:send_buttons"
    assert r.send_video("tg:1", "f", "c") == "tg:send_video"
    assert r.send_template("1", "t", "en", []) == "wa:send_template"
    assert r.download_media("tg:F", "d", 1) == "tg:download_media"
    assert r.requires_templates_outside_window is True
    assert r.health() == {"ok": True, "provider": "wa", "telegram": {"ok": True, "provider": "tg"}}


def test_router_without_telegram():
    r = MessagingRouter(Stub("wa"), None)
    with pytest.raises(RuntimeError, match="TELEGRAM_BOT_TOKEN"):
        r.send_text("tg:1", "x")
    assert "telegram" not in r.health()


# ------------------------------------------------------------------ dev provider
def test_dev_download_media(tmp_path):
    d = DevMessagingProvider()
    with pytest.raises(SendError, match="not found"):
        d.download_media("nope", str(tmp_path / "x"), 10)
    get_storage().put_bytes("dev-media/big", b"x" * 20)
    with pytest.raises(SendError) as ei:
        d.download_media("big", str(tmp_path / "x"), 10)
    assert ei.value.code == "too_large"
    get_storage().put_bytes("dev-media/nomime", b"x")
    assert d.download_media("nomime", str(tmp_path / "x"), 10) == "audio/ogg"


def test_dev_fail_next():
    d = DevMessagingProvider()
    DevMessagingProvider.fail_next = 1
    with pytest.raises(SendError):
        d.send_text("1", "x")
    assert d.send_text("1", "x").startswith("wamid.dev.")
    assert "development" in d.health()["note"]


# ------------------------------------------------------------------ outbound delivery
class Recorder:
    requires_templates_outside_window = True
    supports_media_upload = True

    def __init__(self, video_exc=None, text_exc=None):
        self.calls, self.video_exc, self.text_exc = [], video_exc, text_exc

    def send_text(self, to, text):
        self.calls.append(("text", to, text))
        if self.text_exc:
            raise self.text_exc
        return "t1"

    def send_buttons(self, to, text, buttons):
        self.calls.append(("buttons", to, text, buttons))
        return "b1"

    def send_video(self, to, path, caption):
        self.calls.append(("video", to, caption))
        if self.video_exc:
            raise self.video_exc
        return "v1"

    def send_template(self, to, tpl, lang, params):
        self.calls.append(("template", to, tpl, params))
        return "tpl1"


@pytest.fixture
def conv(db):
    u = User(name="U")
    db.add(u)
    db.flush()
    wa = WhatsAppAccount(wa_id="15550001111", user_id=u.id, last_inbound_at=utcnow())
    db.add(wa)
    db.flush()
    c = Conversation(whatsapp_account_id=wa.id, user_id=u.id)
    db.add(c)
    db.commit()
    return c


def add_msg(db, conv, kind="text", body="hi", payload=None, status="pending"):
    m = Message(conversation_id=conv.id, direction="outbound", kind=kind, body=body, payload=payload, status=status)
    db.add(m)
    db.commit()
    return m


def add_asset(db, conv, size=10, deleted=False):
    get_storage().put_bytes("reels/a.mp4", b"x" * size)
    a = GeneratedAsset(user_id=conv.user_id, kind="video", storage_key="reels/a.mp4", mime_type="video/mp4",
                       size_bytes=size, deleted_at=utcnow() if deleted else None)
    db.add(a)
    db.commit()
    return a


def close_window(db, conv):
    conv.whatsapp_account.last_inbound_at = utcnow() - timedelta(days=2)
    db.commit()


def test_in_window(db, conv):
    assert outbox.in_window(conv)
    close_window(db, conv)
    assert not outbox.in_window(conv)
    conv.whatsapp_account.last_inbound_at = None
    assert not outbox.in_window(conv)


def test_outbox_skips_blank_text(db, conv):
    o = outbox.Outbox(db, conv)
    assert o.text("   ") is None and o.ids == []


def test_deliver_buttons(db, conv):
    m = add_msg(db, conv, "interactive", "Pick", {"buttons": [{"id": "a", "title": "A"}]})
    r = Recorder()
    outbox.deliver_message(db, m, r)
    assert r.calls == [("buttons", "15550001111", "Pick", [("a", "A")])] and m.status == "sent"


def test_deliver_outside_window_uses_template(db, conv, monkeypatch):
    monkeypatch.setattr(get_settings(), "whatsapp_template_params", 1)
    close_window(db, conv)
    m = add_msg(db, conv, body="x" * 1000)
    r = Recorder()
    outbox.deliver_message(db, m, r)
    assert r.calls[0][0] == "template" and len(r.calls[0][3][0]) == 900
    assert m.payload["via_template"] and m.status == "sent" and m.channel_message_id == "tpl1"


def test_deliver_video_outside_window_held_once(db, conv, monkeypatch):
    monkeypatch.setattr(get_settings(), "whatsapp_template_params", 1)
    close_window(db, conv)
    m = add_msg(db, conv, "video", "cap", {"asset_id": "a", "link_text": "My reel"})
    r = Recorder()
    outbox.deliver_message(db, m, r)
    outbox.deliver_message(db, m, r)
    assert [c[0] for c in r.calls] == ["template"] and r.calls[0][3] == ["My reel"]
    assert m.status == "held" and m.payload["template_sent"]


def test_deliver_outside_window_no_template_needed(db, conv):
    close_window(db, conv)
    m = add_msg(db, conv)
    r = Recorder()
    r.requires_templates_outside_window = False
    outbox.deliver_message(db, m, r)
    assert r.calls[0][0] == "text"


def test_deliver_video_missing_asset(db, conv):
    m = add_msg(db, conv, "video", "c", {"asset_id": "missing"})
    with pytest.raises(SendError, match="asset missing"):
        outbox.deliver_message(db, m, Recorder())
    a = add_asset(db, conv, deleted=True)
    m2 = add_msg(db, conv, "video", "c", {"asset_id": a.id})
    with pytest.raises(SendError):
        outbox.deliver_message(db, m2, Recorder())


def test_deliver_video_upload(db, conv):
    a = add_asset(db, conv)
    m = add_msg(db, conv, "video", "cap", {"asset_id": a.id, "link_text": "L"})
    r = Recorder()
    outbox.deliver_message(db, m, r)
    assert r.calls == [("video", "15550001111", "cap")] and m.payload["delivered_as"] == "video"
    assert "url" in m.payload


def test_deliver_video_rejected_falls_back_to_link(db, conv):
    a = add_asset(db, conv)
    m = add_msg(db, conv, "video", "cap", {"asset_id": a.id, "link_text": "Ready:", "after_text": "Changes?"})
    r = Recorder(video_exc=SendError("format", retryable=False))
    outbox.deliver_message(db, m, r)
    assert m.kind == "link" and m.payload["attachment_error"] == "format"
    body = r.calls[-1][2]
    assert body.startswith("Ready:\n") and body.endswith("Changes?")


def test_deliver_video_retryable_error_propagates(db, conv):
    a = add_asset(db, conv)
    m = add_msg(db, conv, "video", "cap", {"asset_id": a.id})
    with pytest.raises(SendError):
        outbox.deliver_message(db, m, Recorder(video_exc=SendError("503", retryable=True)))


def test_deliver_video_too_big_sends_link(db, conv, monkeypatch):
    monkeypatch.setattr(get_settings(), "whatsapp_max_video_bytes", 5)
    a = add_asset(db, conv, size=10)
    m = add_msg(db, conv, "video", "cap", {"asset_id": a.id})
    r = Recorder()
    outbox.deliver_message(db, m, r)
    assert r.calls[0][0] == "text" and m.kind == "link" and m.payload["delivered_as"] == "link"


def test_deliver_uses_router_channel(db, conv):
    wa, t = Recorder(), Recorder()
    t.requires_templates_outside_window = False
    close_window(db, conv)
    conv.whatsapp_account.wa_id = "tg:5"
    db.commit()
    m = add_msg(db, conv)
    outbox.deliver_message(db, m, MessagingRouter(wa, t))
    assert wa.calls == [] and t.calls[0][0] == "text"


def test_deliver_batch_skips_done_and_held(db, conv):
    sent = add_msg(db, conv, status="sent")
    failed = add_msg(db, conv, status="failed")
    close_window(db, conv)
    held = add_msg(db, conv, status="held")
    r = Recorder()
    outbox.deliver_batch(db, [sent.id, failed.id, held.id, "missing"], r)
    assert r.calls == []


def test_deliver_batch_stops_on_retryable(db, conv):
    a, b = add_msg(db, conv, body="a"), add_msg(db, conv, body="b")
    r = Recorder(text_exc=SendError("down", retryable=True))
    with pytest.raises(RetryableError):
        outbox.deliver_batch(db, [a.id, b.id], r)
    assert len(r.calls) == 1 and db.get(Message, a.id).status == "pending" and db.get(Message, a.id).error == "down"


def test_deliver_batch_permanent_failure_continues(db, conv):
    a, b = add_msg(db, conv, body="a"), add_msg(db, conv, body="b")
    r = Recorder(text_exc=SendError("bad number", retryable=False))
    outbox.deliver_batch(db, [a.id, b.id], r)
    assert db.get(Message, a.id).status == "failed" and db.get(Message, b.id).status == "failed"
    assert len(r.calls) == 2
