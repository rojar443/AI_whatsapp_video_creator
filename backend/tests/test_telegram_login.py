"""Telegram website login (link / QR) and the admin user list."""
from datetime import timedelta

import pytest

from app.api.deps import limiter
from app.config import get_settings
from app.models import LoginToken, User, VideoProject, WhatsAppAccount, utcnow
from tests.conftest import drain, fresh_client, register
from tests.test_telegram import CHAT, feed, msg, tg  # noqa: F401  (tg is a fixture)

BOT = "ReelChatTestBot"


@pytest.fixture(autouse=True)
def _bot_config(monkeypatch):
    s = get_settings()
    monkeypatch.setattr(s, "telegram_bot_token", "TESTTOKEN")
    monkeypatch.setattr(s, "telegram_bot_username", BOT)
    monkeypatch.setattr(s, "registration_enabled", False)


def start(client, accept=True):
    return client.post("/api/auth/telegram/start", json={"accept_terms": accept})


def token_of(r) -> str:
    link = r.json()["deep_link"]
    assert link.startswith(f"https://t.me/{BOT}?start=login_")
    return link.split("start=login_")[1]


def status(client, cid):
    return client.get("/api/auth/telegram/status", params={"id": cid})


def press_start(db, tgf, token, chat=CHAT, mid=1, name="Asha"):
    update = {"update_id": 5000 + mid, "message": {
        "message_id": mid, "date": 1_790_000_000, "chat": {"id": chat, "type": "private"},
        "from": {"id": chat, "first_name": name}, "text": f"/start login_{token}"}}
    feed(db, tgf, update)


# --------------------------------------------------------------------------- start
def test_start_returns_deep_link_and_cookie(client):
    r = start(client)
    assert r.status_code == 200 and r.json()["expires_in_minutes"] == 15
    assert len(token_of(r)) == 32 and "rc_login" in client.cookies


def test_start_requires_terms_and_bot_and_rate_limit(client, monkeypatch):
    assert start(client, accept=False).status_code == 422
    codes = [start(client).status_code for _ in range(10)]  # the call above used 1 of 10
    assert codes[:9] == [200] * 9 and codes[9] == 429
    limiter.reset()
    monkeypatch.setattr(get_settings(), "telegram_bot_token", None)
    assert fresh_client().post("/api/auth/telegram/start", json={"accept_terms": True}).status_code == 503


# --------------------------------------------------------------------------- confirm + handoff
def test_bot_only_user_logs_in_and_keeps_work(client, db, tg):  # noqa: F811
    feed(db, tg, msg(1, text="hello"))  # first contact creates an anonymous profile
    user = db.query(User).one()
    assert user.is_anonymous and user.credits_balance == 2
    r = start(client)
    cid = r.json()["challenge_id"]
    assert status(client, cid).json() == {"status": "pending"}
    press_start(db, tg, token_of(r), mid=2)
    assert any("signed in to the website" in t for t in tg.texts())
    s = status(client, cid).json()
    assert s["status"] == "confirmed" and s["user"]["id"] == user.id
    client.headers["x-csrf-token"] = s["csrf_token"]
    me = client.get("/api/me").json()
    assert me["id"] == user.id and me["credits"] == 10 and me["role"] == "user"
    db.expire_all()
    assert db.get(User, user.id).is_anonymous is False  # promoted to a regular account
    assert db.query(WhatsAppAccount).one().status == "linked"


def test_new_chat_without_prior_message_gets_account(client, db, tg):  # noqa: F811
    r = start(client)
    press_start(db, tg, token_of(r), chat=424242, name="Ravi")
    s = status(client, r.json()["challenge_id"]).json()
    assert s["status"] == "confirmed" and s["user"]["name"] == "Ravi"
    assert db.query(User).count() == 1


def test_token_is_single_use_and_browser_bound(client, db, tg):  # noqa: F811
    r = start(client)
    cid, token = r.json()["challenge_id"], token_of(r)
    press_start(db, tg, token)
    other = fresh_client()
    assert status(other, cid).status_code == 404  # no poll secret
    assert status(client, cid).json()["status"] == "confirmed"
    assert status(client, cid).status_code == 404  # secret cleared after redemption
    assert db.query(LoginToken).one().status == "consumed"
    press_start(db, tg, token, mid=3)  # replaying the link does nothing
    assert any("didn't work" in t for t in tg.texts())


def test_expired_and_unknown_tokens(client, db, tg):  # noqa: F811
    r = start(client)
    row = db.query(LoginToken).one()
    row.expires_at = utcnow() - timedelta(minutes=1)
    db.commit()
    press_start(db, tg, token_of(r))
    assert any("didn't work" in t for t in tg.texts())
    assert status(client, r.json()["challenge_id"]).json() == {"status": "expired"}
    press_start(db, tg, "x" * 32, mid=4)
    assert db.query(LoginToken).one().status == "pending"


def test_suspended_user_denied(client, db, tg):  # noqa: F811
    feed(db, tg, msg(1, text="hello"))
    user = db.query(User).one()
    user.status = "suspended"
    db.commit()
    r = start(client)
    press_start(db, tg, token_of(r), mid=2)
    assert status(client, r.json()["challenge_id"]).status_code == 403
    assert db.query(LoginToken).one().status == "denied"


def test_login_link_ignored_outside_telegram_private_chats(client, db, tg):  # noqa: F811
    r = start(client)
    group = {"update_id": 77, "message": {"message_id": 1, "date": 1_790_000_000, "chat": {"id": -100, "type": "group"},
                                          "from": {"id": CHAT, "first_name": "A"}, "text": f"/start login_{token_of(r)}"}}
    feed(db, tg, group)
    assert db.query(LoginToken).one().status == "pending"


# --------------------------------------------------------------------------- admin
def login_as_admin(client, db, tg, monkeypatch, chat=900001):  # noqa: F811
    monkeypatch.setattr(get_settings(), "admin_telegram_ids", f"{chat}, 123")
    r = start(client)
    press_start(db, tg, token_of(r), chat=chat, name="Owner")
    s = status(client, r.json()["challenge_id"]).json()
    client.headers["x-csrf-token"] = s["csrf_token"]
    return s["user"]


def test_admin_by_telegram_id_and_user_list(client, db, tg, monkeypatch, fake_pipeline):  # noqa: F811
    feed(db, tg, msg(1, text="hello"))  # a bot-only user who never opened the website
    register_c = fresh_client()
    monkeypatch.setattr(get_settings(), "registration_enabled", True)
    register(register_c, email="web@example.com", phone="")
    monkeypatch.setattr(get_settings(), "registration_enabled", False)
    admin = login_as_admin(client, db, tg, monkeypatch)
    assert admin["role"] == "admin"
    r = client.get("/api/admin/users")
    assert r.status_code == 200 and r.json()["total"] == 3
    by_name = {i["name"]: i for i in r.json()["items"]}
    bot_only = next(i for i in r.json()["items"] if i["channels"] and i["channels"][0]["id"] == str(CHAT))
    assert bot_only["channels"][0]["kind"] == "telegram" and bot_only["registered"] is False
    assert by_name["Owner"]["role"] == "admin" and by_name["Owner"]["last_seen"]
    assert any(i["email"] == "web@example.com" and i["channels"][0]["kind"] == "web" for i in r.json()["items"])
    assert "body" not in str(r.json()) and "url" not in str(r.json())  # no messages or media links
    assert client.get("/api/admin/users", params={"q": "web@"}).json()["total"] == 1
    assert client.get("/api/admin/users", params={"limit": 1, "offset": 1}).json()["items"].__len__() == 1


def test_admin_counts_projects_and_reels(client, db, tg, monkeypatch):  # noqa: F811
    feed(db, tg, msg(1, text="hello"))
    uid = db.query(User).one().id
    db.add_all([VideoProject(user_id=uid, status="completed"), VideoProject(user_id=uid, status="completed"),
                VideoProject(user_id=uid, status="failed")])
    db.commit()
    login_as_admin(client, db, tg, monkeypatch)
    row = next(i for i in client.get("/api/admin/users").json()["items"] if i["id"] == uid)
    assert row["projects"] == 3 and row["reels"] == 2


def test_non_admin_forbidden(client, db, tg):  # noqa: F811
    r = start(client)
    press_start(db, tg, token_of(r))
    s = status(client, r.json()["challenge_id"]).json()
    client.headers["x-csrf-token"] = s["csrf_token"]
    assert s["user"]["role"] == "user"
    assert client.get("/api/admin/users").status_code == 403
    assert fresh_client().get("/api/admin/users").status_code == 401


def test_empty_admin_list_grants_nothing(client, db, tg, monkeypatch):  # noqa: F811
    monkeypatch.setattr(get_settings(), "admin_telegram_ids", "")
    r = start(client)
    press_start(db, tg, token_of(r))
    assert status(client, r.json()["challenge_id"]).json()["user"]["role"] == "user"


def test_admin_suspend_and_credits(client, db, tg, monkeypatch):  # noqa: F811
    feed(db, tg, msg(1, text="hello"))
    uid = db.query(User).one().id
    admin = login_as_admin(client, db, tg, monkeypatch)
    assert client.patch(f"/api/admin/users/{uid}", json={"credits_delta": 5}).json()["credits"] == 7
    assert client.patch(f"/api/admin/users/{uid}", json={"credits_delta": -50}).status_code == 400
    assert client.patch(f"/api/admin/users/{uid}", json={"status": "suspended"}).status_code == 200
    drain()
    feed(db, tg, msg(2, text="make me a reel"))
    assert any("suspended" in t for t in tg.texts())
    assert client.patch(f"/api/admin/users/{admin['id']}", json={"status": "suspended"}).status_code == 400
    assert client.patch("/api/admin/users/nope", json={"status": "active"}).status_code == 404
    from app.models import AuditLog

    assert db.query(AuditLog).filter_by(action="admin.user_updated").count() == 2


# --------------------------------------------------------------------------- dev login by Telegram id
def test_dev_login_by_telegram_id(client, db, monkeypatch):
    monkeypatch.setattr(get_settings(), "admin_telegram_ids", "123456789")
    r = client.post("/api/dev/telegram/login", json={"tg_id": "123456789", "name": "Me"})
    assert r.status_code == 200 and r.json()["user"]["role"] == "admin" and r.json()["user"]["credits"] == 10
    client.headers["x-csrf-token"] = r.json()["csrf_token"]
    assert client.get("/api/admin/users").status_code == 200
    again = fresh_client().post("/api/dev/telegram/login", json={"tg_id": "123456789"})
    assert again.json()["user"]["id"] == r.json()["user"]["id"]  # same Telegram id, same account
    assert client.post("/api/dev/telegram/login", json={"tg_id": "not-a-number"}).status_code == 422


def test_dev_login_disabled_in_production(client, monkeypatch):
    monkeypatch.setattr(get_settings(), "app_env", "production")
    assert client.post("/api/dev/telegram/login", json={"tg_id": "123456789"}).status_code == 404


# --------------------------------------------------------------------------- everyone who tried the bot
def test_everyone_who_messaged_the_bot_is_listed_with_timestamps(client, db, tg, monkeypatch):  # noqa: F811
    from app.telegram_poller import handle_update

    # a chat that messaged but whose message the worker has not processed yet still shows up
    handle_update(db, tg.provider, msg(1, text="/start"))
    other = {"update_id": 2, "message": {"message_id": 2, "date": 1_790_000_000, "chat": {"id": 888, "type": "private"},
                                         "from": {"id": 888, "first_name": "Ravi"}, "text": "hi"}}
    handle_update(db, tg.provider, other)
    admin = login_as_admin(client, db, tg, monkeypatch)
    items = client.get("/api/admin/users").json()["items"]
    tried = [i for i in items if i["id"] != admin["id"]]
    assert {i["channels"][0]["id"] for i in tried} == {str(CHAT), "888"}
    for i in tried:
        assert i["registered"] is False and i["messages"] == 1
        assert i["first_seen"] and i["last_seen"]
    ravi = next(i for i in tried if i["channels"][0]["id"] == "888")
    assert ravi["name"] == "Ravi" and ravi["last_seen"].startswith("2026-09-21")  # the message's own timestamp


def test_admin_channel_filter(client, db, tg, monkeypatch):  # noqa: F811
    feed(db, tg, msg(1, text="hello"))
    monkeypatch.setattr(get_settings(), "registration_enabled", True)
    register(fresh_client(), email="web@example.com", phone="")
    login_as_admin(client, db, tg, monkeypatch)
    assert client.get("/api/admin/users", params={"channel": "web"}).json()["total"] == 1
    assert client.get("/api/admin/users", params={"channel": "telegram"}).json()["total"] == 2  # bot user + admin
    assert client.get("/api/admin/users", params={"channel": "whatsapp"}).json()["total"] == 0
    assert client.get("/api/admin/users", params={"channel": "fax"}).status_code == 422


# --------------------------------------------------------------------------- token usage + talked-to-bot filter
def test_admin_shows_token_usage_and_talked_filter(client, db, tg, monkeypatch):  # noqa: F811
    from app.models import UsageRecord

    feed(db, tg, msg(1, text="hello"))  # CHAT talks to the bot
    talker = db.query(User).one()
    quiet = User(name="Quiet", email="q@example.com", credits_balance=0)  # registered, never messaged
    db.add(quiet)
    db.flush()
    db.add_all([
        UsageRecord(user_id=talker.id, kind="llm", provider="gemini", quantity=1200, unit="tokens", estimated_cost_usd=0.0021),
        UsageRecord(user_id=talker.id, kind="llm", provider="gemini", quantity=300, unit="tokens", estimated_cost_usd=0.0007),
        UsageRecord(user_id=talker.id, kind="transcription", provider="mock", quantity=5, unit="seconds", estimated_cost_usd=0.01),
        UsageRecord(user_id=quiet.id, kind="llm", provider="gemini", quantity=50, unit="tokens", estimated_cost_usd=0.0001),
    ])
    db.commit()
    admin = login_as_admin(client, db, tg, monkeypatch)  # pressing Start in the bot is also talking to it
    items = client.get("/api/admin/users").json()["items"]
    row = next(i for i in items if i["id"] == talker.id)
    assert row["tokens"] == 1500 and abs(row["cost_usd"] - 0.0128) < 1e-9  # only llm rows count as tokens
    assert next(i for i in items if i["id"] == quiet.id)["tokens"] == 50
    only = client.get("/api/admin/users", params={"talked": "true"}).json()
    assert {i["id"] for i in only["items"]} == {talker.id, admin["id"]} and only["total"] == 2  # not the quiet one
    s = client.get("/api/admin/summary").json()
    assert s["tokens"] == 1550 and s["talked_to_bot"] == 2 and s["users"] == 3
    assert {p["provider"] for p in s["by_provider"]} == {"gemini", "mock"}
    assert fresh_client().get("/api/admin/summary").status_code == 401
