"""Web API edge cases: registration/login/verification, Telegram web login, account endpoints, projects and files."""
import time
from datetime import timedelta
from urllib.parse import parse_qs, urlparse

import pytest

from app import providers
from app.config import get_settings
from app.messaging.base import SendError
from app.models import (
    Conversation,
    GeneratedAsset,
    GenerationJob,
    LoginToken,
    User,
    VideoPreferences,
    VideoProject,
    WhatsAppAccount,
    utcnow,
)
from app.security import _hmac, sign_asset_url, sign_payload
from app.storage import get_storage
from tests.conftest import fresh_client, register

PW = "correct horse battery"


@pytest.fixture
def settings(monkeypatch):
    s = get_settings()

    def set_(**kw):
        for k, v in kw.items():
            monkeypatch.setattr(s, k, v)

    return set_


def body(**kw):
    b = {"name": "Asha", "email": "asha@example.com", "password": PW, "phone": "+1 555 765 4321",
         "accept_terms": True}
    b.update(kw)
    return b


# ------------------------------------------------------------------ register
def test_register_disabled(client, settings):
    settings(registration_enabled=False)
    assert client.post("/api/auth/register", json=body()).status_code == 403


def test_register_requires_terms(client):
    assert client.post("/api/auth/register", json=body(accept_terms=False)).status_code == 422


def test_register_bad_phone(client):
    r = client.post("/api/auth/register", json=body(phone="12"))
    assert r.status_code == 422 and "international format" in r.json()["detail"]


def test_register_without_phone(client, db):
    assert client.post("/api/auth/register", json=body(phone="  ")).status_code == 201
    assert db.query(User).one().phone_e164 is None


def test_register_duplicate_email_case_insensitive(client):
    client.post("/api/auth/register", json=body())
    r = fresh_client().post("/api/auth/register", json=body(email="ASHA@example.com"))
    assert r.status_code == 409


def test_register_grants_credits_and_hides_dev_url_in_prod(client, db, settings):
    settings(app_env="production")
    r = client.post("/api/auth/register", json=body())
    assert r.status_code == 201 and "dev_verification_url" not in r.json()
    assert db.query(User).one().credits_balance == get_settings().default_credits


@pytest.mark.parametrize("bad", [{"password": "short"}, {"email": "not-an-email"}, {"name": ""}])
def test_register_validation(client, bad):
    assert client.post("/api/auth/register", json=body(**bad)).status_code == 422


# ------------------------------------------------------------------ login / logout
def test_login_flow(client, db):
    register(client)
    c = fresh_client()
    assert c.post("/api/auth/login", json={"email": "asha@example.com", "password": "wrong"}).status_code == 401
    assert c.post("/api/auth/login", json={"email": "nobody@example.com", "password": PW}).status_code == 401
    r = c.post("/api/auth/login", json={"email": "ASHA@example.com", "password": PW})
    assert r.status_code == 200 and r.json()["csrf_token"]
    assert "rc_session" in "".join(r.headers.get_list("set-cookie")) or c.cookies


def test_login_suspended(client, db):
    register(client)
    db.query(User).update({"status": "suspended"})
    db.commit()
    r = fresh_client().post("/api/auth/login", json={"email": "asha@example.com", "password": PW})
    assert r.status_code == 403


def test_login_anonymous_user_cannot_login(client, db):
    db.add(User(email="anon@example.com", is_anonymous=True, password_hash=None))
    db.commit()
    assert client.post("/api/auth/login", json={"email": "anon@example.com", "password": PW}).status_code == 401


def test_logout_revokes_session(client):
    register(client)
    assert client.post("/api/auth/logout").json() == {"ok": True}
    assert client.get("/api/me").status_code == 401


# ------------------------------------------------------------------ email verification
def test_verify_email_errors(client, db):
    assert client.post("/api/auth/verify-email", json={"token": "garbage"}).json()["detail"] == "Invalid link"
    expired = sign_payload({"uid": "x", "email": "e"}, "verify-email", -10)
    assert client.post("/api/auth/verify-email", json={"token": expired}).json()["detail"] == "This link has expired"
    unknown = sign_payload({"uid": "nope", "email": "e"}, "verify-email", 60)
    assert client.post("/api/auth/verify-email", json={"token": unknown}).status_code == 400


def test_verify_email_changed_address_rejected(client, db):
    register(client)
    u = db.query(User).one()
    tok = sign_payload({"uid": u.id, "email": "old@example.com"}, "verify-email", 60)
    assert client.post("/api/auth/verify-email", json={"token": tok}).status_code == 400


def test_verify_email_idempotent(client, db):
    data = register(client)
    tok = parse_qs(urlparse(data["dev_verification_url"]).query)["token"][0]
    assert client.post("/api/auth/verify-email", json={"token": tok}).json() == {"ok": True}


def test_resend_verification(client, db, settings):
    r = client.post("/api/auth/register", json=body())
    client.headers["x-csrf-token"] = r.json()["csrf_token"]
    out = client.post("/api/auth/resend-verification").json()
    assert out["ok"] and "dev_verification_url" in out
    db.query(User).update({"email_verified_at": utcnow()})
    db.commit()
    assert client.post("/api/auth/resend-verification").json() == {"ok": True, "already_verified": True}


def test_resend_verification_prod_hides_url(client, settings):
    r = client.post("/api/auth/register", json=body())
    client.headers["x-csrf-token"] = r.json()["csrf_token"]
    settings(app_env="production")
    assert client.post("/api/auth/resend-verification").json() == {"ok": True}


# ------------------------------------------------------------------ Telegram web login
def test_telegram_start_unavailable(client, settings):
    settings(telegram_bot_token=None)
    assert client.post("/api/auth/telegram/start", json={"accept_terms": True}).status_code == 503


def test_telegram_start_requires_terms(client, settings):
    settings(telegram_bot_token="1:x", telegram_bot_username="bot")
    assert client.post("/api/auth/telegram/start", json={"accept_terms": False}).status_code == 422


@pytest.fixture
def tg_login(client, settings):
    settings(telegram_bot_token="1:x", telegram_bot_username="reelbot")
    r = client.post("/api/auth/telegram/start", json={"accept_terms": True}).json()
    assert r["deep_link"].startswith("https://t.me/reelbot?start=login_")
    return r["challenge_id"]


def status(client, cid):
    return client.get("/api/auth/telegram/status", params={"id": cid})


def test_telegram_status_unknown_or_wrong_browser(client, tg_login):
    assert status(client, "nope").status_code == 404
    assert status(fresh_client(), tg_login).status_code == 404  # no poll-secret cookie


def test_telegram_status_pending_and_expired(client, db, tg_login):
    assert status(client, tg_login).json() == {"status": "pending"}
    db.query(LoginToken).update({"expires_at": utcnow() - timedelta(seconds=1)})
    db.commit()
    assert status(client, tg_login).json() == {"status": "expired"}


def test_telegram_status_denied(client, db, tg_login):
    db.query(LoginToken).update({"status": "denied"})
    db.commit()
    assert status(client, tg_login).status_code == 403


def confirm(db, cid, **user_kw):
    u = User(name="T", **user_kw)
    db.add(u)
    db.flush()
    db.query(LoginToken).filter_by(id=cid).update({"status": "confirmed", "user_id": u.id})
    db.commit()
    return u


def test_telegram_status_confirmed_once(client, db, tg_login):
    confirm(db, tg_login)
    r = status(client, tg_login)
    assert r.json()["status"] == "confirmed" and r.json()["csrf_token"]
    assert client.get("/api/me").status_code == 200
    assert status(client, tg_login).status_code == 404  # poll cookie cleared: single use


def test_telegram_status_confirmed_suspended_user(client, db, tg_login):
    confirm(db, tg_login, status="suspended")
    assert status(client, tg_login).status_code == 403


def test_telegram_status_lost_race(client, db, tg_login, monkeypatch):
    confirm(db, tg_login)
    from sqlalchemy.orm import Session

    real = Session.execute

    def execute(self, stmt, *a, **kw):
        res = real(self, stmt, *a, **kw)
        if getattr(stmt, "is_update", False) and stmt.table.name == "login_tokens":
            res.rowcount = 0  # another poll consumed it first
        return res

    monkeypatch.setattr(Session, "execute", execute)
    try:
        assert status(client, tg_login).json() == {"status": "expired"}
    except AttributeError:
        pytest.skip("result rowcount not writable on this driver")


# ------------------------------------------------------------------ /api/me
def test_me_requires_login(client):
    assert client.get("/api/me").status_code == 401


def test_usage_ledger(client, db):
    register(client)
    out = client.get("/api/me/usage").json()
    assert out["credits"] == get_settings().default_credits
    assert out["ledger"][0]["kind"] == "credit_grant" and out["ledger"][0]["credits"] > 0


def test_link_whatsapp_disabled(client, settings):
    register(client)
    settings(whatsapp_enabled=False)
    assert client.post("/api/me/whatsapp/link").status_code == 404


def test_link_whatsapp_requires_phone(client, db):
    register(client)
    db.query(User).update({"phone_e164": None})
    db.commit()
    assert client.post("/api/me/whatsapp/link").status_code == 422


def test_link_whatsapp_owned_by_other_account(client, db):
    register(client)
    other = User(name="O", email="o@x.test")
    db.add(other)
    db.flush()
    db.add(WhatsAppAccount(wa_id="15557654321", user_id=other.id, status="linked"))
    db.commit()
    assert client.post("/api/me/whatsapp/link").status_code == 409


def test_link_whatsapp_anonymous_owner_ok(client, db):
    register(client)
    anon = User(name="", is_anonymous=True)
    db.add(anon)
    db.flush()
    db.add(WhatsAppAccount(wa_id="15557654321", user_id=anon.id))
    db.commit()
    r = client.post("/api/me/whatsapp/link").json()
    assert r["status"] == "pending" and len(r["code"]) == 6 and r["message"] == f"LINK {r['code']}"


def test_link_whatsapp_already_linked(client, db):
    register(client)
    u = db.query(User).one()
    db.add(WhatsAppAccount(wa_id="15557654321", user_id=u.id, status="linked"))
    db.commit()
    assert client.post("/api/me/whatsapp/link").json() == {"status": "linked"}


def test_link_whatsapp_creates_account_and_conversation(client, db):
    register(client)
    client.post("/api/me/whatsapp/link")
    wa = db.query(WhatsAppAccount).one()
    assert wa.pending_user_id and db.query(Conversation).filter_by(whatsapp_account_id=wa.id).count() == 1


@pytest.mark.parametrize("path,wa_id", [("/api/me/whatsapp", "15557654321"), ("/api/me/telegram", "tg:5")])
def test_unlink(client, db, path, wa_id):
    register(client)
    assert client.delete(path).json() == {"ok": True}  # nothing linked
    u = db.query(User).one()
    wa = WhatsAppAccount(wa_id=wa_id, user_id=u.id, status="linked")
    db.add(wa)
    db.flush()
    p = VideoProject(user_id=u.id)
    db.add(p)
    db.flush()
    conv = Conversation(whatsapp_account_id=wa.id, user_id=u.id, active_project_id=p.id)
    db.add(conv)
    db.commit()
    client.delete(path)
    db.expire_all()
    assert db.get(WhatsAppAccount, wa.id).user_id is None and db.get(WhatsAppAccount, wa.id).status == "anonymous"
    assert db.get(Conversation, conv.id).active_project_id is None


def test_unlink_without_conversation(client, db):
    register(client)
    u = db.query(User).one()
    db.add(WhatsAppAccount(wa_id="15557654321", user_id=u.id, status="linked"))
    db.commit()
    assert client.delete("/api/me/whatsapp").json() == {"ok": True}


class FakeTG:
    def __init__(self, exc=None):
        self.exc = exc

    def me(self):
        if self.exc:
            raise self.exc
        return {"username": "reelbot"}


def test_telegram_link(client, db):
    register(client)
    providers._overrides["telegram"] = None
    assert client.post("/api/me/telegram/link").status_code == 503
    providers._overrides["telegram"] = FakeTG(SendError("down"))
    assert client.post("/api/me/telegram/link").status_code == 502
    providers._overrides["telegram"] = FakeTG()
    r = client.post("/api/me/telegram/link").json()
    assert r["bot"] == "@reelbot" and r["url"].startswith("https://t.me/reelbot?start=link_")
    u = db.query(User).one()
    db.add(WhatsAppAccount(wa_id="tg:9", user_id=u.id, status="linked"))
    db.commit()
    assert client.post("/api/me/telegram/link").json() == {"status": "linked"}


def test_delete_account(client, db, monkeypatch):
    register(client)
    u = db.query(User).one()
    wa = WhatsAppAccount(wa_id="15557654321", user_id=u.id)
    db.add(wa)
    db.flush()
    db.add(Conversation(whatsapp_account_id=wa.id, user_id=u.id))
    get_storage().put_bytes("reels/u.mp4", b"x")
    db.add_all([GeneratedAsset(user_id=u.id, kind="video", storage_key="reels/u.mp4", mime_type="video/mp4"),
                GeneratedAsset(user_id=u.id, kind="video", storage_key="", mime_type="video/mp4")])
    db.commit()
    assert client.post("/api/me/delete", json={"password": "wrong"}).status_code == 403
    assert client.post("/api/me/delete", json={"password": PW}).json() == {"ok": True}
    db.expire_all()
    assert db.query(User).count() == 0 and db.query(WhatsAppAccount).count() == 0
    assert db.query(Conversation).count() == 0 and not get_storage().exists("reels/u.mp4")


# ------------------------------------------------------------------ projects
@pytest.fixture
def owner(client, db):
    register(client)
    return db.query(User).one()


def test_projects_list_and_isolation(client, db, owner):
    other = User(name="O")
    db.add(other)
    db.flush()
    mine = VideoProject(user_id=owner.id, title="Mine")
    theirs = VideoProject(user_id=other.id, title="Theirs")
    db.add_all([mine, theirs])
    db.commit()
    assert [p["title"] for p in client.get("/api/projects").json()["items"]] == ["Mine"]
    assert client.get(f"/api/projects/{mine.id}").status_code == 200
    assert client.get(f"/api/projects/{theirs.id}").status_code == 404
    assert client.get("/api/projects", params={"limit": 500}).status_code == 422


@pytest.mark.parametrize("job_status,code,detail", [
    (None, 409, "Nothing to retry"), ("processing", 409, "This video is already generating"),
])
def test_retry_project_errors(client, db, owner, job_status, code, detail):
    p = VideoProject(user_id=owner.id, status="failed")
    db.add(p)
    db.flush()
    if job_status:
        db.add(GenerationJob(project_id=p.id, user_id=owner.id, revision=1, idempotency_key="k", status=job_status))
    db.commit()
    r = client.post(f"/api/projects/{p.id}/retry")
    assert r.status_code == code and r.json()["detail"] == detail


def test_retry_project_insufficient_and_ok(client, db, owner):
    p = VideoProject(user_id=owner.id, status="failed")
    db.add(p)
    db.flush()
    db.add(VideoPreferences(project_id=p.id, revision=1, plan={"total_seconds": 30}))
    j = GenerationJob(project_id=p.id, user_id=owner.id, revision=1, idempotency_key="k", status="failed")
    db.add(j)
    owner.credits_balance = 0
    db.commit()
    assert client.post(f"/api/projects/{p.id}/retry").status_code == 402
    db.query(User).update({"credits_balance": 10})
    db.commit()
    assert client.post(f"/api/projects/{p.id}/retry").json() == {"ok": True, "job_id": j.id}


def asset(db, user, key="reels/a.mp4", mime="video/mp4", data=b"MP4DATA", deleted=False):
    if data is not None:
        get_storage().put_bytes(key, data)
    a = GeneratedAsset(user_id=user.id, kind="video", storage_key=key, mime_type=mime,
                       deleted_at=utcnow() if deleted else None)
    db.add(a)
    db.commit()
    return a


def test_fresh_link(client, db, owner):
    a = asset(db, owner)
    assert "/api/files/" in client.get(f"/api/assets/{a.id}/link").json()["url"]
    gone = asset(db, owner, deleted=True)
    assert client.get(f"/api/assets/{gone.id}/link").status_code == 404
    other = User(name="O")
    db.add(other)
    db.commit()
    assert client.get(f"/api/assets/{asset(db, other).id}/link").status_code == 404


def file_url(asset_id, ttl=60):
    u = urlparse(sign_asset_url(asset_id, ttl))
    return u.path + "?" + u.query


def test_download_file(client, db, owner):
    a = asset(db, owner)
    r = fresh_client().get(file_url(a.id))
    assert r.status_code == 200 and r.content == b"MP4DATA"
    assert "attachment" in r.headers["content-disposition"] and r.headers["cache-control"] == "private, max-age=600"
    r = fresh_client().get(file_url(a.id) + "&inline=1")
    assert "attachment" not in r.headers.get("content-disposition", "")


@pytest.mark.parametrize("mime,ext", [("application/json", "json"), ("application/x-subrip", "srt"),
                                      ("text/weird", "bin")])
def test_download_filename_extension(client, db, owner, mime, ext):
    a = asset(db, owner, mime=mime)
    assert f".{ext}" in fresh_client().get(file_url(a.id)).headers["content-disposition"]


def test_download_expired_and_invalid(client, db, owner):
    a = asset(db, owner)
    r = fresh_client().get(file_url(a.id, ttl=-5))
    assert r.status_code == 410 and "expired" in r.json()["detail"]
    exp = int(time.time()) + 60
    assert fresh_client().get(f"/api/files/{a.id}?exp={exp}&sig=bad").status_code == 403


def test_download_missing(client, db, owner):
    assert fresh_client().get(file_url("nope")).status_code == 404
    gone = asset(db, owner, deleted=True)
    assert fresh_client().get(file_url(gone.id)).status_code == 404
    lost = asset(db, owner, key="reels/lost.mp4", data=None)
    assert fresh_client().get(file_url(lost.id)).status_code == 404


def test_download_presigned_redirect(client, db, owner, monkeypatch):
    from app.api import projects

    a = asset(db, owner)

    class S3Like:
        def presigned_url(self, key, ttl, filename):
            return f"https://s3.test/{key}?f={filename}"

    monkeypatch.setattr(projects, "get_storage", lambda: S3Like())
    r = fresh_client().get(file_url(a.id), follow_redirects=False)
    assert r.status_code == 302 and r.headers["location"].startswith("https://s3.test/reels/a.mp4")


def test_download_storage_misconfigured(client, db, owner, monkeypatch):
    from app.api import projects

    a = asset(db, owner)

    class Odd:
        def presigned_url(self, *a):
            return None

    monkeypatch.setattr(projects, "get_storage", lambda: Odd())
    assert fresh_client().get(file_url(a.id)).status_code == 500


def test_pipeline_file(client, tmp_path):
    s = get_settings()
    from pathlib import Path

    work = Path(s.work_dir)
    work.mkdir(parents=True, exist_ok=True)
    f = work / "frame.jpg"
    f.write_bytes(b"JPEG")
    ok = sign_payload({"p": str(f)}, "pipeline-file", 60)
    assert client.get(f"/api/pipeline-files/{ok}").content == b"JPEG"
    outside = tmp_path / "secret.txt"
    outside.write_text("x")
    assert client.get(f"/api/pipeline-files/{sign_payload({'p': str(outside)}, 'pipeline-file', 60)}").status_code == 404
    assert client.get(f"/api/pipeline-files/{sign_payload({'p': str(work)}, 'pipeline-file', 60)}").status_code == 404
    traversal = sign_payload({"p": str(work / ".." / ".." / "etc" / "passwd")}, "pipeline-file", 60)
    assert client.get(f"/api/pipeline-files/{traversal}").status_code == 404
    assert client.get(f"/api/pipeline-files/{sign_payload({'p': str(f)}, 'pipeline-file', -5)}").status_code == 410
    assert client.get("/api/pipeline-files/forged.sig").status_code == 403
    assert client.get(f"/api/pipeline-files/{sign_payload({'p': str(f)}, 'verify-email', 60)}").status_code == 403
