"""WhatsApp login, account identity and re-authentication (openspec change add-dual-login)."""
from datetime import timedelta

from conftest import (
    fresh_client,
    last_reply,
    link_whatsapp,
    register,
    wa_text,
)

from app.jobs.tasks import run_pending
from app.models import LoginChallenge, User, WhatsAppAccount, utcnow

PHONE = "+91 98765 43210"
WA = "919876543210"
LONG_PW = "a long enough password"


def start(client, phone=PHONE, accept=True):
    return client.post("/api/auth/whatsapp/start", json={"phone": phone, "accept_terms": accept})


def send(client, db, wa_id, text):
    wa_text(client, wa_id, text)
    run_pending(db)
    return last_reply(db, wa_id).body


def status(client, cid):
    return client.get("/api/auth/whatsapp/status", params={"id": cid})


def whatsapp_login(client, db, phone=PHONE, wa_id=WA):
    r = start(client, phone)
    assert r.status_code == 200, r.text
    d = r.json()
    send(client, db, wa_id, d["message"])
    s = status(client, d["challenge_id"])
    assert s.status_code == 200, s.text
    return d, s.json()


def reauth(client, db, wa_id=WA):
    d = client.post("/api/me/reauth/start").json()
    send(client, db, wa_id, d["message"])
    return d["challenge_id"]


# --------------------------------------------------------------------------- start
def test_start_returns_code_and_link(client):
    d = start(client).json()
    assert len(d["code"]) == 6 and d["message"] == f"LOGIN {d['code']}"
    assert d["wa_me_url"].endswith(f"?text=LOGIN%20{d['code']}") and d["expires_in_minutes"] <= 15
    assert "rc_login" in client.cookies


def test_start_validation(client):
    assert start(client, "12").status_code == 422
    assert start(client, accept=False).status_code == 422


def test_start_rate_limited_per_number(client):
    codes = [start(client).status_code for _ in range(6)]
    assert codes[:5] == [200] * 5 and codes[5] == 429


# --------------------------------------------------------------------------- confirm + handoff
def test_whatsapp_signup_creates_account_and_session(client, db):
    d, s = whatsapp_login(client, db)
    assert s["status"] == "confirmed"
    user = s["user"]
    assert user["email"] is None and user["phone"] == "+" + WA and user["credits"] == 10
    assert user["whatsapp"]["status"] == "linked"
    client.headers["x-csrf-token"] = s["csrf_token"]
    assert client.get("/api/me").json()["id"] == user["id"]
    assert "signed in" in last_reply(db, WA).body


def test_session_is_single_use(client, db):
    d, s = whatsapp_login(client, db)
    assert s["status"] == "confirmed"
    assert status(client, d["challenge_id"]).status_code == 404  # poll secret was cleared on redemption
    replay = fresh_client()
    replay.cookies.set("rc_login", "stolen-guess", path="/api/auth")
    assert status(replay, d["challenge_id"]).status_code == 404
    assert db.query(LoginChallenge).one().status == "consumed"


def test_pending_until_confirmed(client, db):
    d = start(client).json()
    s = status(client, d["challenge_id"])
    assert s.json() == {"status": "pending"} and "rc_session" not in client.cookies


def test_other_browser_cannot_redeem(client, db):
    d = start(client).json()
    send(client, db, WA, d["message"])
    other = fresh_client()
    assert status(other, d["challenge_id"]).status_code == 404
    assert "rc_session" not in other.cookies
    assert status(client, d["challenge_id"]).json()["status"] == "confirmed"


def test_code_from_other_number_rejected(client, db):
    d = start(client).json()
    assert "didn't work" in send(client, db, "919000000001", d["message"])
    assert status(client, d["challenge_id"]).json()["status"] == "pending"


def test_wrong_code_and_lockout(client, db):
    d = start(client).json()
    wrong = "000000" if d["code"] != "000000" else "111111"
    for _ in range(5):
        assert "didn't work" in send(client, db, WA, f"LOGIN {wrong}")
    assert "didn't work" in send(client, db, WA, d["message"])  # locked out even with the right code
    assert status(client, d["challenge_id"]).json()["status"] == "pending"


def test_expired_code(client, db):
    d = start(client).json()
    ch = db.get(LoginChallenge, d["challenge_id"])
    ch.expires_at = utcnow() - timedelta(minutes=1)
    db.commit()
    assert "didn't work" in send(client, db, WA, d["message"])
    assert status(client, d["challenge_id"]).json()["status"] == "expired"


def test_login_command_formats(client, db):
    d = start(client).json()
    assert "signed in" in send(client, db, WA, f"login: {d['code']}")


def test_second_login_same_number_same_account(client, db):
    _, s1 = whatsapp_login(client, db)
    c2 = fresh_client()
    _, s2 = whatsapp_login(c2, db)
    assert s1["user"]["id"] == s2["user"]["id"]
    assert db.query(User).count() == 1


def test_suspended_account_denied(client, db):
    _, s = whatsapp_login(client, db)
    u = db.get(User, s["user"]["id"])
    u.status = "suspended"
    db.commit()
    c2 = fresh_client()
    d = start(c2).json()
    assert "can't sign in" in send(c2, db, WA, d["message"])
    assert status(c2, d["challenge_id"]).status_code == 403


# --------------------------------------------------------------------------- identity
def test_email_login_unaffected_and_passwordless_gives_generic_error(client, db):
    register(client)
    ok = fresh_client().post("/api/auth/login", json={"email": "asha@example.com", "password": "correct horse battery"})
    assert ok.status_code == 200
    u = db.query(User).one()
    u.password_hash = None
    db.commit()
    bad = fresh_client().post("/api/auth/login", json={"email": "asha@example.com", "password": LONG_PW})
    unknown = fresh_client().post("/api/auth/login", json={"email": "no@example.com", "password": LONG_PW})
    assert bad.status_code == unknown.status_code == 401 and bad.json() == unknown.json()


def test_unverified_duplicate_number_allowed_at_signup(client):
    register(client, email="a@example.com")
    register(fresh_client(), email="b@example.com")  # same typed phone


def test_first_to_verify_wins(client, db):
    register(client, email="a@example.com")
    c2 = fresh_client()
    register(c2, email="b@example.com")
    link_whatsapp(client, db)
    assert c2.post("/api/me/whatsapp/link").status_code == 409


def test_linked_email_user_logs_in_by_whatsapp_same_account(client, db):
    reg = register(client)
    link_whatsapp(client, db)
    _, s = whatsapp_login(fresh_client(), db)
    assert s["user"]["id"] == reg["user"]["id"] and s["user"]["email"] == "asha@example.com"
    assert db.query(User).count() == 1


def test_unlinked_email_user_gets_separate_account(client, db):
    reg = register(client)
    _, s = whatsapp_login(fresh_client(), db)
    assert s["user"]["id"] != reg["user"]["id"] and s["user"]["email"] is None
    assert client.post("/api/me/whatsapp/link").status_code == 409  # number is now claimed


def test_whatsapp_only_cannot_unlink(client, db):
    _, s = whatsapp_login(client, db)
    client.headers["x-csrf-token"] = s["csrf_token"]
    assert client.delete("/api/me/whatsapp").status_code == 409


def test_email_user_can_still_unlink(client, db):
    register(client)
    link_whatsapp(client, db)
    assert client.delete("/api/me/whatsapp").status_code == 200


# --------------------------------------------------------------------------- reauth
def test_whatsapp_only_delete_needs_fresh_check(client, db):
    _, s = whatsapp_login(client, db)
    client.headers["x-csrf-token"] = s["csrf_token"]
    assert client.post("/api/me/delete", json={}).status_code == 403
    cid = reauth(client, db)
    assert client.get(f"/api/me/reauth/{cid}").json()["status"] == "confirmed"
    assert client.post("/api/me/delete", json={"reauth_challenge_id": cid}).status_code == 200
    assert db.query(User).count() == 0
    assert db.query(WhatsAppAccount).count() == 0


def test_reauth_single_use_and_unconfirmed_rejected(client, db):
    _, s = whatsapp_login(client, db)
    client.headers["x-csrf-token"] = s["csrf_token"]
    d = client.post("/api/me/reauth/start").json()
    assert client.post("/api/me/delete", json={"reauth_challenge_id": d["challenge_id"]}).status_code == 403
    send(client, db, WA, d["message"])
    pw = {"email": "new@example.com", "password": LONG_PW, "reauth_challenge_id": d["challenge_id"]}
    assert client.post("/api/me/credentials", json=pw).status_code == 200
    assert client.post("/api/me/delete", json={"reauth_challenge_id": d["challenge_id"]}).status_code == 403


def test_reauth_from_wrong_number_rejected(client, db):
    _, s = whatsapp_login(client, db)
    client.headers["x-csrf-token"] = s["csrf_token"]
    d = client.post("/api/me/reauth/start").json()
    assert "didn't work" in send(client, db, "919000000002", d["message"])
    assert client.post("/api/me/delete", json={"reauth_challenge_id": d["challenge_id"]}).status_code == 403


def test_password_delete_still_works_and_wrong_password_403(client):
    register(client)
    assert client.post("/api/me/delete", json={"password": "nope nope nope"}).status_code == 403
    assert client.post("/api/me/delete", json={"password": "correct horse battery"}).status_code == 200


def test_add_email_and_password_then_email_login(client, db):
    _, s = whatsapp_login(client, db)
    client.headers["x-csrf-token"] = s["csrf_token"]
    cid = reauth(client, db)
    r = client.post("/api/me/credentials", json={"email": "New@Example.com", "password": LONG_PW,
                                                 "reauth_challenge_id": cid})
    assert r.status_code == 200 and r.json()["user"]["email"] == "new@example.com"
    assert r.json()["user"]["email_verified"] is False and "dev_verification_url" in r.json()
    ok = fresh_client().post("/api/auth/login", json={"email": "new@example.com", "password": LONG_PW})
    assert ok.status_code == 200 and ok.json()["user"]["id"] == s["user"]["id"]


def test_add_email_taken_by_other_account_conflicts_without_merge(client, db):
    register(client, email="taken@example.com", phone="+44 7700 900123")
    other = fresh_client()
    _, s = whatsapp_login(other, db)
    other.headers["x-csrf-token"] = s["csrf_token"]
    cid = reauth(other, db)
    r = other.post("/api/me/credentials", json={"email": "taken@example.com", "password": LONG_PW,
                                                "reauth_challenge_id": cid})
    assert r.status_code == 409
    assert db.query(User).count() == 2
