"""App wiring (startup guards, middleware, health), task dispatch semantics and webhook status handling."""
import asyncio
import json
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from app import main
from app.api import webhooks
from app.config import get_settings
from app.jobs import dispatch
from app.jobs.dispatch import INLINE, REGISTRY, RetryableError, TaskDef
from app.messaging.base import InboundMessage, build_inbound_payload
from app.models import Conversation, Message, WhatsAppAccount
from app.security import meta_signature
from tests.conftest import post_webhook


@pytest.fixture
def settings(monkeypatch):
    s = get_settings()

    def set_(**kw):
        for k, v in kw.items():
            monkeypatch.setattr(s, k, v)

    return set_


def run_lifespan():
    async def go():
        async with main.lifespan(main.app):
            pass

    asyncio.run(go())


# ------------------------------------------------------------------ startup guards
def test_lifespan_rejects_dev_secret_in_production(settings):
    settings(app_env="production", secret_key="dev-insecure")
    with pytest.raises(RuntimeError, match="SECRET_KEY"):
        run_lifespan()


@pytest.mark.parametrize("secret,token", [("dev-app-secret", "real"), ("real", "dev-verify-token")])
def test_lifespan_rejects_dev_whatsapp_secrets(settings, secret, token):
    settings(app_env="production", secret_key="prod-key", whatsapp_enabled=True,
             whatsapp_app_secret=secret, whatsapp_verify_token=token)
    with pytest.raises(RuntimeError, match="WHATSAPP_APP_SECRET"):
        run_lifespan()


def test_lifespan_production_ok_without_whatsapp(settings):
    settings(app_env="production", secret_key="prod-key", whatsapp_enabled=False, messaging_provider="dev",
             whatsapp_app_secret="dev-app-secret")
    run_lifespan()


def test_lifespan_meta_provider_requires_secrets(settings):
    settings(app_env="production", secret_key="prod-key", whatsapp_enabled=False, messaging_provider="meta",
             whatsapp_app_secret="dev-app-secret")
    with pytest.raises(RuntimeError):
        run_lifespan()


def test_lifespan_survives_playbook_sync_failure(monkeypatch):
    from app import playbooks

    monkeypatch.setattr(playbooks, "sync_to_db", lambda db: 1 / 0)
    run_lifespan()


# ------------------------------------------------------------------ middleware / handlers / health
def test_security_headers(client):
    r = client.get("/api/config")
    assert r.headers["x-content-type-options"] == "nosniff"
    assert r.headers["x-frame-options"] == "DENY"
    assert r.headers["referrer-policy"] == "strict-origin-when-cross-origin"


def test_hsts_when_cookie_secure(settings):
    settings(cookie_secure=True)
    app = main.create_app()
    with TestClient(app) as c:
        assert "max-age=31536000" in c.get("/api/config").headers["strict-transport-security"]


def test_unhandled_exception_returns_500(settings):
    app = main.create_app()

    @app.get("/boom")
    def boom():
        raise RuntimeError("secret detail")

    with TestClient(app, raise_server_exceptions=False) as c:
        r = c.get("/boom")
    assert r.status_code == 500 and r.json() == {"detail": "Internal error"}


def test_health_ok(client):
    assert client.get("/api/health").json() == {"ok": True, "db": True, "env": "test"}


def test_health_db_down(client, monkeypatch):
    class Bad:
        def connect(self):
            raise OSError("db down")

    monkeypatch.setattr(main, "get_engine", lambda: Bad())
    assert client.get("/api/health").json()["ok"] is False


def test_provider_health_dev_only(client, settings):
    assert "ai" in client.get("/api/health/providers").json()
    settings(app_env="production")
    assert client.get("/api/health/providers").json() == {"detail": "available in development only"}


def test_public_config(client, settings):
    settings(telegram_bot_token=None)
    c = client.get("/api/config").json()
    assert c["dev_mode"] is True and c["telegram_bot"] is None
    settings(telegram_bot_token="1:x", telegram_bot_username="reelbot")
    assert client.get("/api/config").json()["telegram_bot"] == "reelbot"


# ------------------------------------------------------------------ dispatch
@pytest.fixture
def fake_task():
    calls = {"n": 0, "final": []}

    def fn(x, fail=None):
        calls["n"] += 1
        if fail == "retry":
            raise RetryableError("try again")
        if fail == "crash":
            raise ValueError("bug")

    REGISTRY["t_test"] = TaskDef(fn, max_retries=2, on_final_failure=lambda *a, error="": calls["final"].append(error))
    REGISTRY["t_nohook"] = TaskDef(fn)
    yield calls
    REGISTRY.pop("t_test")
    REGISTRY.pop("t_nohook")


def test_backoff_doubles(settings):
    settings(job_retry_base_seconds=3)
    assert [dispatch.backoff(a) for a in (1, 2, 3)] == [3, 6, 12]


def test_enqueue_after_commit_only(db, fake_task):
    dispatch.enqueue(db, "t_test", 1)
    assert INLINE.ready == []
    db.commit()
    assert INLINE.ready == [("t_test", [1], 1)]


def test_enqueue_dropped_on_rollback(db, fake_task):
    db.add(WhatsAppAccount(wa_id="rollback"))
    db.flush()  # a real transaction is open, as in every caller
    dispatch.enqueue(db, "t_test", 1)
    db.rollback()
    assert db.query(WhatsAppAccount).count() == 0
    db.commit()
    assert INLINE.ready == []


def test_enqueue_kept_after_nested_rollback(db, fake_task):
    dispatch.enqueue(db, "t_test", 1)
    try:
        with db.begin_nested():
            raise ValueError
    except ValueError:
        pass
    db.commit()
    assert INLINE.ready == [("t_test", [1], 1)]


def test_enqueue_without_db_and_countdown(fake_task):
    dispatch.enqueue(None, "t_test", 2, countdown=5)
    assert INLINE.delayed == [("t_test", [2], 1)] and INLINE.ready == []


def test_enqueue_unknown_loads_registry(monkeypatch):
    loaded = []
    monkeypatch.setattr(dispatch, "_ensure_loaded", lambda: loaded.append(1))
    monkeypatch.setitem(REGISTRY, "known", TaskDef(lambda: None))
    dispatch.enqueue(None, "known")
    assert loaded == []
    dispatch.enqueue(None, "not_yet_imported")
    assert loaded == [1]


def test_send_via_celery(settings, fake_task, monkeypatch):
    from app.jobs import celery_app

    sent = []
    monkeypatch.setattr(celery_app.celery_app, "send_task", lambda name, **kw: sent.append((name, kw)))
    settings(task_backend="celery")
    REGISTRY["t_test"].queue = "media"
    dispatch._send("t_test", (1, 2), 30, 3)
    assert sent == [("reelchat.run_media", {"args": ["t_test", [1, 2], 3], "queue": "media", "countdown": 30})]


def test_execute_retries_then_final_failure(fake_task, settings):
    settings(job_retry_base_seconds=1)
    dispatch._send("t_test", (1, "retry"), None)
    assert INLINE.drain(include_delayed=True) == 3
    assert fake_task["n"] == 3 and fake_task["final"] == ["try again"]


def test_execute_retry_callback(fake_task):
    delays = []
    dispatch.execute("t_test", [1, "retry"], 1, retry=delays.append)
    assert delays == [1] and INLINE.ready == [] and INLINE.delayed == []


def test_execute_crash_calls_final_failure(fake_task):
    dispatch.execute("t_test", [1, "crash"], 1)
    assert fake_task["final"] == ["ValueError: bug"]


def test_execute_crash_without_hook_raises(fake_task):
    with pytest.raises(ValueError):
        dispatch.execute("t_nohook", [1, "crash"], 1)


def test_execute_retry_without_hook_gives_up(fake_task):
    dispatch.execute("t_nohook", [1, "retry"], 1)
    assert INLINE.delayed == []


def test_inline_run_delayed_by_name(fake_task):
    dispatch._send("t_test", (1,), 5)
    dispatch._send("t_nohook", (2,), 5)
    assert INLINE.run_delayed("t_test") == 1
    assert [t[0] for t in INLINE.delayed] == ["t_nohook"]
    assert INLINE.run_delayed() == 1


def test_inline_drain_limit(fake_task):
    for i in range(5):
        dispatch._send("t_test", (i,), None)
    assert INLINE.drain(limit=2) == 2 and len(INLINE.ready) == 3


# ------------------------------------------------------------------ webhooks
def statuses_payload(*rows):
    return {"entry": [{"changes": [{"value": {"statuses": list(rows)}}]}]}


@pytest.fixture
def outbound(db):
    wa = WhatsAppAccount(wa_id="15550001111")
    db.add(wa)
    db.flush()
    conv = Conversation(whatsapp_account_id=wa.id)
    db.add(conv)
    db.flush()
    m = Message(conversation_id=conv.id, direction="outbound", kind="text", status="sent", channel_message_id="wamid.X")
    db.add(m)
    db.commit()
    return m


@pytest.mark.parametrize("seq,final", [(["delivered", "read"], "read"), (["read", "delivered"], "read"),
                                       (["sent"], "sent"), (["weird"], "sent")])
def test_status_updates_only_move_forward(client, db, outbound, seq, final):
    for st in seq:
        post_webhook(client, statuses_payload({"id": "wamid.X", "status": st}))
    db.expire_all()
    assert db.get(Message, outbound.id).status == final


def test_status_failed_records_error(client, db, outbound):
    r = post_webhook(client, statuses_payload({"id": "wamid.X", "status": "failed",
                                               "errors": [{"code": 131026, "title": "Undeliverable"}]}))
    assert r.json() == {"accepted": 0, "statuses": 1}
    db.expire_all()
    m = db.get(Message, outbound.id)
    assert m.status == "failed" and m.error == "131026: Undeliverable"


def test_status_for_unknown_or_inbound_ignored(client, db, outbound):
    outbound.direction = "inbound"
    db.commit()
    post_webhook(client, statuses_payload({"id": "wamid.X", "status": "read"}, {"id": "nope", "status": "read"}))
    db.expire_all()
    assert db.get(Message, outbound.id).status == "sent"


def test_webhook_too_large(client):
    raw = b"{" + b" " * (webhooks.MAX_BODY + 1) + b"}"
    r = client.post("/api/webhooks/whatsapp", content=raw,
                    headers={"x-hub-signature-256": meta_signature(raw, "test-app-secret")})
    assert r.status_code == 413


def test_webhook_invalid_json(client):
    raw = b"{not json"
    r = client.post("/api/webhooks/whatsapp", content=raw,
                    headers={"x-hub-signature-256": meta_signature(raw, "test-app-secret")})
    assert r.status_code == 400


def test_webhook_empty_body(client):
    r = client.post("/api/webhooks/whatsapp", content=b"",
                    headers={"x-hub-signature-256": meta_signature(b"", "test-app-secret")})
    assert r.json() == {"accepted": 0, "statuses": 0}


def test_ingest_keeps_latest_inbound_time_and_name(db):
    old = datetime(2026, 1, 1, tzinfo=UTC)
    new = old + timedelta(hours=1)
    webhooks.ingest_message(db, InboundMessage(wa_id="1", message_id="a", kind="text", timestamp=new, text="x",
                                               profile_name="New Name"))
    webhooks.ingest_message(db, InboundMessage(wa_id="1", message_id="b", kind="text", timestamp=old, text="y"))
    db.commit()
    wa = db.query(WhatsAppAccount).filter_by(wa_id="1").one()
    assert wa.last_inbound_at.replace(tzinfo=UTC) == new and wa.profile_name == "New Name"


def test_ingest_future_timestamp_clamped(db):
    future = datetime.now(UTC) + timedelta(days=1)
    webhooks.ingest_message(db, InboundMessage(wa_id="2", message_id="c", kind="text", timestamp=future, text="x"))
    db.commit()
    wa = db.query(WhatsAppAccount).filter_by(wa_id="2").one()
    assert wa.last_inbound_at.replace(tzinfo=UTC) < future


def test_ingest_payload_and_body(db):
    row = webhooks.ingest_message(db, InboundMessage(
        wa_id="3", message_id="d", kind="video", timestamp=datetime.now(UTC), media_id="M",
        media_mime="video/mp4", caption="trip"))
    assert row.body == "trip" and row.payload == {"media_mime": "video/mp4", "caption": "trip"}
    assert webhooks.ingest_message(db, InboundMessage(wa_id="3", message_id="d", kind="text",
                                                      timestamp=datetime.now(UTC))) is None


def test_ingest_uses_existing_user_after_lock(db, monkeypatch):
    """If another worker created the user between the check and the lock, reuse it."""
    from app.models import User

    u = User(name="X", is_anonymous=True)
    db.add(u)
    db.flush()
    wa = WhatsAppAccount(wa_id="4")
    db.add(wa)
    db.commit()
    real_refresh = db.refresh

    def refresh(obj, **kw):
        real_refresh(obj, **kw)
        if isinstance(obj, WhatsAppAccount):
            obj.user_id = u.id

    monkeypatch.setattr(db, "refresh", refresh)
    webhooks.ingest_message(db, InboundMessage(wa_id="4", message_id="e", kind="text", timestamp=datetime.now(UTC)))
    conv = db.query(Conversation).filter_by(whatsapp_account_id=wa.id).one()
    assert conv.user_id == u.id and db.query(User).count() == 1


def test_get_or_create_conversation_race(db, monkeypatch):
    """A concurrent insert of the same wa_id is resolved by re-reading the winner's row."""
    from sqlalchemy.exc import IntegrityError

    winner = WhatsAppAccount(wa_id="5")
    db.add(winner)
    db.commit()
    real_scalar = db.scalar
    calls = {"n": 0}

    def scalar(stmt, *a, **kw):
        calls["n"] += 1
        if calls["n"] == 1:
            return None  # first lookup misses (row not yet visible)
        return real_scalar(stmt, *a, **kw)

    monkeypatch.setattr(db, "scalar", scalar)
    conv = webhooks.get_or_create_conversation(db, "5", None)
    assert conv.whatsapp_account_id == winner.id
