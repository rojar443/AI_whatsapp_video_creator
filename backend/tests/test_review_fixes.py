"""End-to-end coverage for the review fixes: one anonymous user per new chat, batched admin counts,
and the Telegram login user resolution shared by the bot login and the dev login."""
import uuid
from datetime import timedelta

import pytest
from sqlalchemy import event

from app.api.webhooks import get_or_create_conversation, ingest_message
from app.config import get_settings
from app.db import get_engine, get_sessionmaker
from app.messaging.base import InboundMessage, build_inbound_payload
from app.models import AuditLog, Conversation, Message, User, WhatsAppAccount, utcnow
from app.services import accounts
from tests.conftest import WA, drain, fresh_client, post_webhook, wa_text
from tests.test_telegram import CHAT, feed, msg, tg  # noqa: F401  (tg is a fixture)
from tests.test_telegram_login import _bot_config, login_as_admin, press_start, start, status, token_of  # noqa: F401


def inbound(wa_id=WA, text="hi", message_id=None, name="Asha") -> InboundMessage:
    return InboundMessage(wa_id=wa_id, message_id=message_id or f"wamid.{uuid.uuid4().hex}", kind="text", text=text,
                          profile_name=name, timestamp=utcnow())


def dev_login(client, tg_id, name="Dev user"):
    return client.post("/api/dev/telegram/login", json={"tg_id": str(tg_id), "name": name})


# --------------------------------------------------------------------------- 5: one anonymous user per new chat
def test_first_whatsapp_message_creates_one_anonymous_user(client, db):
    assert wa_text(client, "hello").status_code == 200
    user = db.query(User).one()
    assert user.is_anonymous and user.credits_balance == 2  # ANON_CREDITS
    wa = db.query(WhatsAppAccount).one()
    conv = db.query(Conversation).one()
    assert wa.user_id == conv.user_id == user.id
    first = db.query(Message).filter_by(direction="inbound").one()
    assert first.user_id == user.id
    assert db.query(AuditLog).filter_by(action="user.anonymous_created").count() == 1


def test_later_messages_reuse_the_user(client, db):
    for text in ("hello", "make a reel", "thanks"):
        wa_text(client, text)
    drain()
    assert db.query(User).count() == 1
    uid = db.query(User).one().id
    assert {m.user_id for m in db.query(Message).filter_by(direction="inbound")} == {uid}


def test_redelivered_webhook_is_stored_once(client, db):
    payload = build_inbound_payload(WA, "wamid.same", text="hello")
    assert post_webhook(client, payload).status_code == 200
    assert post_webhook(client, payload).status_code == 200
    assert db.query(Message).filter_by(direction="inbound").count() == 1
    assert db.query(User).count() == 1


def test_two_chats_get_two_users(client, db):
    wa_text(client, "hi", wa_id="15550000001")
    wa_text(client, "hi", wa_id="15550000002")
    users = db.query(User).all()
    assert len(users) == 2
    assert {w.user_id for w in db.query(WhatsAppAccount)} == {u.id for u in users}


def test_ingest_uses_user_created_by_a_parallel_request(db):
    """Request A read the account before request B gave it a user: A must reuse B's user, not make another."""
    get_or_create_conversation(db, WA, "Asha")  # account + conversation exist, no user yet
    db.commit()
    stale = db.query(WhatsAppAccount).filter_by(wa_id=WA).one()  # request A reads the account: no user yet
    seen = stale.id
    other = get_sessionmaker()()
    try:  # request B wins the race
        wa_b = other.get(WhatsAppAccount, seen)
        winner = accounts.create_anonymous(other, wa_b)
        other.commit()
        winner_id = winner.id
    finally:
        other.close()
    assert stale.user_id is None  # A still holds the stale row
    row = ingest_message(db, inbound())
    db.commit()
    assert row is not None and row.user_id == winner_id
    assert db.query(User).count() == 1
    assert db.query(Conversation).one().user_id == winner_id


def test_ingest_for_known_account_without_user_creates_exactly_one(db):
    get_or_create_conversation(db, WA, "Asha")
    db.commit()
    for _ in range(3):
        assert ingest_message(db, inbound()) is not None
    db.commit()
    assert db.query(User).count() == 1
    assert db.query(Message).count() == 3


def test_ingest_duplicate_id_returns_none_and_creates_nothing_new(db):
    first = ingest_message(db, inbound(message_id="wamid.dup"))
    db.commit()
    assert first is not None
    assert ingest_message(db, inbound(message_id="wamid.dup")) is None
    db.commit()
    assert db.query(User).count() == 1 and db.query(Message).count() == 1


def test_ingest_updates_profile_name_and_last_inbound(db):
    ingest_message(db, inbound(name="Asha"))
    ingest_message(db, inbound(name="Asha Nair"))
    db.commit()
    wa = db.query(WhatsAppAccount).one()
    assert wa.profile_name == "Asha Nair" and wa.last_inbound_at is not None


# --------------------------------------------------------------------------- 7: batched inbound message counts
def _seed_users(db, n):
    """n anonymous chat users; user i has sent i+1 inbound messages and one outbound reply."""
    ids = []
    for i in range(n):
        for _ in range(i + 1):
            ingest_message(db, inbound(wa_id=f"1555100{i:04d}", name=f"User{i}"))
        conv = db.query(Conversation).join(WhatsAppAccount).filter(WhatsAppAccount.wa_id == f"1555100{i:04d}").one()
        db.add(Message(conversation_id=conv.id, user_id=conv.user_id, direction="outbound", kind="text", body="ok",
                       status="sent"))
        ids.append(conv.user_id)
    db.commit()
    return ids


def test_admin_message_counts_are_per_user_and_inbound_only(client, db, tg, monkeypatch):  # noqa: F811
    ids = _seed_users(db, 4)
    admin = login_as_admin(client, db, tg, monkeypatch)
    items = {i["id"]: i for i in client.get("/api/admin/users").json()["items"]}
    for n, uid in enumerate(ids):
        assert items[uid]["messages"] == n + 1  # the outbound reply is not counted
    assert items[admin["id"]]["messages"] == 1  # pressing Start


def test_admin_message_count_zero_for_silent_user(client, db, tg, monkeypatch):  # noqa: F811
    quiet = User(name="Quiet", email="quiet@example.com", credits_balance=0)
    db.add(quiet)
    db.commit()
    login_as_admin(client, db, tg, monkeypatch)
    row = next(i for i in client.get("/api/admin/users").json()["items"] if i["id"] == quiet.id)
    assert row["messages"] == 0


def test_admin_message_counts_follow_pagination(client, db, tg, monkeypatch):  # noqa: F811
    ids = _seed_users(db, 5)
    login_as_admin(client, db, tg, monkeypatch)
    expected = {uid: n + 1 for n, uid in enumerate(ids)}
    seen = {}
    for offset in range(0, 6, 2):
        for i in client.get("/api/admin/users", params={"limit": 2, "offset": offset}).json()["items"]:
            seen[i["id"]] = i["messages"]
    for uid, count in expected.items():
        assert seen[uid] == count


def _count_queries(fn):
    n = 0

    def before(*_):
        nonlocal n
        n += 1

    engine = get_engine()
    event.listen(engine, "before_cursor_execute", before)
    try:
        fn()
    finally:
        event.remove(engine, "before_cursor_execute", before)
    return n


def test_admin_user_list_does_not_count_messages_per_row(client, db, tg, monkeypatch):  # noqa: F811
    login_as_admin(client, db, tg, monkeypatch)
    _seed_users(db, 2)
    small = _count_queries(lambda: client.get("/api/admin/users").raise_for_status())
    assert all(ingest_message(db, inbound(wa_id=f"1555200{i:04d}")) for i in range(4))
    db.commit()
    large = _count_queries(lambda: client.get("/api/admin/users").raise_for_status())
    # 4 more rows; each row still loads its channels, projects and reels (3 queries) but no message count
    assert large - small == 4 * 3


# --------------------------------------------------------------------------- 9: shared Telegram login resolution
def _tg_conv(db, tg_id=CHAT, name="Asha"):
    conv = get_or_create_conversation(db, f"tg:{tg_id}", name)
    return conv, conv.whatsapp_account


def test_resolve_creates_anonymous_user_and_links_conversation(db):
    conv, wa = _tg_conv(db)
    user = accounts.resolve_telegram_login_user(db, wa, conv)
    assert user.is_anonymous and user.role == "user"
    assert wa.user_id == conv.user_id == user.id


def test_resolve_reuses_existing_user_and_relinks_conversation(db):
    conv, wa = _tg_conv(db)
    user = accounts.create_anonymous(db, wa)
    conv.user_id = None  # e.g. a conversation created before the account had a user
    assert accounts.resolve_telegram_login_user(db, wa, conv).id == user.id
    assert conv.user_id == user.id
    assert db.query(User).count() == 1


def test_resolve_grants_admin_only_to_listed_ids(db, monkeypatch):
    monkeypatch.setattr(get_settings(), "admin_telegram_ids", f"{CHAT}")
    conv, wa = _tg_conv(db)
    assert accounts.resolve_telegram_login_user(db, wa, conv).role == "admin"
    conv2, wa2 = _tg_conv(db, tg_id=555)
    assert accounts.resolve_telegram_login_user(db, wa2, conv2).role == "user"


def test_resolve_never_demotes_an_admin(db, monkeypatch):
    conv, wa = _tg_conv(db)
    user = accounts.create_anonymous(db, wa)
    user.role = "admin"
    monkeypatch.setattr(get_settings(), "admin_telegram_ids", "")
    assert accounts.resolve_telegram_login_user(db, wa, conv).role == "admin"


def test_resolve_ignores_admin_ids_for_whatsapp_numbers(db, monkeypatch):
    monkeypatch.setattr(get_settings(), "admin_telegram_ids", WA)
    conv = get_or_create_conversation(db, WA, "Asha")  # a WhatsApp number that happens to match
    assert accounts.resolve_telegram_login_user(db, conv.whatsapp_account, conv).role == "user"


def test_resolve_returns_suspended_user_for_caller_to_refuse(db):
    conv, wa = _tg_conv(db)
    user = accounts.create_anonymous(db, wa)
    user.status = "suspended"
    resolved = accounts.resolve_telegram_login_user(db, wa, conv)
    assert resolved.id == user.id and resolved.status == "suspended" and resolved.is_anonymous  # not promoted


def test_dev_login_promotes_bot_user_and_links_conversation(client, db, tg):  # noqa: F811
    feed(db, tg, msg(1, text="hello"))  # bot-only anonymous profile
    bot_user = db.query(User).one()
    r = dev_login(client, CHAT)
    assert r.status_code == 200
    assert r.json()["user"]["id"] == bot_user.id and r.json()["user"]["credits"] == 10
    db.expire_all()
    assert db.get(User, bot_user.id).is_anonymous is False
    assert db.query(WhatsAppAccount).one().status == "linked"
    assert db.query(Conversation).one().user_id == bot_user.id
    assert db.query(AuditLog).filter_by(action="auth.login").count() == 1


def test_dev_login_relinks_conversation_for_existing_user(client, db):
    conv, wa = _tg_conv(db, tg_id=4242)
    user = accounts.create_anonymous(db, wa)
    conv.user_id = None
    db.commit()
    assert dev_login(client, 4242).status_code == 200
    db.expire_all()
    assert db.query(Conversation).one().user_id == user.id  # the old dev copy left this unset


def test_dev_login_refuses_suspended_user(client, db):
    conv, wa = _tg_conv(db, tg_id=4242)
    user = accounts.create_anonymous(db, wa)
    user.status = "suspended"
    db.commit()
    r = dev_login(client, 4242)
    assert r.status_code == 403
    db.expire_all()
    assert db.get(User, user.id).is_anonymous is True  # refused before promotion
    assert "rc_session" not in r.cookies


def test_dev_login_new_id_creates_registered_user(client, db):
    r = dev_login(client, 31337, name="Newbie")
    assert r.status_code == 200
    user = db.query(User).one()
    assert user.is_anonymous is False and r.json()["user"]["role"] == "user"
    assert db.query(WhatsAppAccount).one().wa_id == "tg:31337"
    assert db.query(Conversation).one().user_id == user.id


@pytest.mark.parametrize("bad", ["", "12", "abc", "12345678901234567", "-123", "1 2 3"])
def test_dev_login_rejects_bad_ids(client, bad):
    assert dev_login(client, bad).status_code == 422


@pytest.mark.parametrize("path", ["bot", "dev"])
def test_bot_and_dev_login_resolve_the_same_way(client, db, tg, monkeypatch, path):  # noqa: F811
    """Both login paths give the same account state for the same Telegram user."""
    monkeypatch.setattr(get_settings(), "admin_telegram_ids", f"{CHAT}")
    feed(db, tg, msg(1, text="hello"))
    uid = db.query(User).one().id
    if path == "bot":
        r = start(client)
        press_start(db, tg, token_of(r), mid=2)
        body = status(client, r.json()["challenge_id"]).json()
    else:
        body = dev_login(client, CHAT).json()
    assert body["user"]["id"] == uid and body["user"]["role"] == "admin"
    db.expire_all()
    user = db.get(User, uid)
    assert user.is_anonymous is False and user.role == "admin"
    assert db.query(WhatsAppAccount).one().status == "linked"
    assert db.query(Conversation).one().user_id == uid
    assert db.query(User).count() == 1


def test_bot_login_refuses_suspended_and_marks_token_denied(client, db, tg):  # noqa: F811
    from app.models import LoginToken

    feed(db, tg, msg(1, text="hello"))
    user = db.query(User).one()
    user.status = "suspended"
    db.commit()
    r = start(client)
    press_start(db, tg, token_of(r), mid=2)
    assert status(client, r.json()["challenge_id"]).status_code == 403
    db.expire_all()
    assert db.query(LoginToken).one().status == "denied"
    assert db.get(User, user.id).is_anonymous is True
    assert fresh_client().get("/api/me").status_code == 401


def test_bot_login_new_chat_then_messages_keep_one_user(client, db, tg):  # noqa: F811
    r = start(client)
    press_start(db, tg, token_of(r), mid=1)  # first ever contact is the login itself
    feed(db, tg, msg(2, text="hello"), msg(3, text="make a reel"))
    assert db.query(User).count() == 1
    uid = db.query(User).one().id
    assert {m.user_id for m in db.query(Message).filter_by(direction="inbound")} == {uid}
    assert status(client, r.json()["challenge_id"]).json()["user"]["id"] == uid


def test_login_token_expiry_still_enforced(client, db, tg):  # noqa: F811
    from app.models import LoginToken

    r = start(client)
    row = db.query(LoginToken).one()
    row.expires_at = utcnow() - timedelta(minutes=1)
    db.commit()
    press_start(db, tg, token_of(r), mid=1)
    assert status(client, r.json()["challenge_id"]).json()["status"] != "confirmed"
