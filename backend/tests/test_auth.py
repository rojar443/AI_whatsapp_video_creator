"""Registration, sessions, CSRF, RBAC and tenant isolation."""
import time

from app.jobs.tasks import run_pending
from app.models import GeneratedAsset, User, VideoProject
from app.security import sign_asset_url
from tests.conftest import (
    confirm_button,
    fresh_client,
    last_reply,
    link_whatsapp,
    outbound,
    register,
    say,
    wa_button,
    wa_text,
)


def test_register_login_logout(client):
    data = register(client)
    assert data["user"]["credits"] == 10
    assert data["user"]["whatsapp"]["status"] == "not_linked"
    assert client.get("/api/me").json()["email_verified"] is True
    assert client.post("/api/auth/logout").status_code == 200
    assert client.get("/api/me").status_code == 401
    r = client.post("/api/auth/login", json={"email": "asha@example.com", "password": "wrong password!"})
    assert r.status_code == 401
    r = client.post("/api/auth/login", json={"email": "ASHA@example.com", "password": "correct horse battery"})
    assert r.status_code == 200


def test_registration_validation(client):
    base = {"name": "A", "email": "a@example.com", "password": "longenough1", "phone": "+15551234567",
            "accept_terms": True}
    assert client.post("/api/auth/register", json=dict(base, phone="12")).status_code == 422
    assert client.post("/api/auth/register", json=dict(base, password="short")).status_code == 422
    assert client.post("/api/auth/register", json=dict(base, accept_terms=False)).status_code == 422
    assert client.post("/api/auth/register", json=base).status_code == 201
    c2 = fresh_client()
    assert c2.post("/api/auth/register", json=base).status_code == 409


def test_password_is_hashed(client, db):
    register(client)
    u = db.query(User).one()
    assert u.password_hash.startswith("$argon2") and "correct horse" not in u.password_hash


def test_csrf_required_for_mutations(client):
    register(client)
    token = client.headers.pop("x-csrf-token")
    assert client.post("/api/me/whatsapp/link").status_code == 403
    assert client.post("/api/me/whatsapp/link", headers={"x-csrf-token": "forged"}).status_code == 403
    assert client.post("/api/me/whatsapp/link", headers={"x-csrf-token": token}).status_code == 200


def test_email_verification_required_to_link(client):
    register(client, verify=False)
    assert client.post("/api/me/whatsapp/link").status_code == 403
    assert client.post("/api/auth/verify-email", json={"token": "garbage.sig"}).status_code == 400


def test_link_code_must_come_from_registered_number(client, db):
    register(client)
    code = client.post("/api/me/whatsapp/link").json()["code"]
    wa_text(client, "15550009999", f"LINK {code}")  # attacker's number
    run_pending(db)
    assert "isn't linked" in last_reply(db, "15550009999").body or "didn't work" in last_reply(db, "15550009999").body
    assert client.get("/api/me").json()["whatsapp"]["status"] == "pending"
    wa_text(client, "919876543210", "LINK 000000")  # wrong code from right number
    run_pending(db)
    assert "didn't work" in last_reply(db, "919876543210").body
    wa_text(client, "919876543210", f"LINK {code}")
    run_pending(db)
    assert client.get("/api/me").json()["whatsapp"]["status"] == "linked"


def test_link_code_brute_force_locked(client, db):
    register(client)
    code = client.post("/api/me/whatsapp/link").json()["code"]
    for i in range(5):
        wa_text(client, "919876543210", f"LINK {(int(code) + 1 + i) % 1000000:06d}")
    wa_text(client, "919876543210", f"LINK {code}")
    run_pending(db)
    assert client.get("/api/me").json()["whatsapp"]["status"] == "pending"


def test_number_linked_to_other_account_cannot_be_claimed(client, db):
    register(client)
    link_whatsapp(client, db)
    other = fresh_client()
    register(other, email="mallory@example.com", phone="+91 98765 43210")
    assert other.post("/api/me/whatsapp/link").status_code == 409


def _make_project(client, db, wa, fake_video):
    say(client, db, wa, "make a funny 15 second youtube short about cats, cinematic, standard quality")
    say(client, db, wa, "yes")
    run_pending(db)
    return db.query(VideoProject).one()


def test_tenant_isolation(client, db, fake_video):
    register(client)
    wa = link_whatsapp(client, db)
    project = _make_project(client, db, wa, fake_video)
    assert project.status == "completed"
    assert len(client.get("/api/projects").json()["items"]) == 1

    other = fresh_client()
    register(other, email="bob@example.com", phone="+15551230000")
    assert other.get("/api/projects").json()["items"] == []
    assert other.get(f"/api/projects/{project.id}").status_code == 404
    assert other.post(f"/api/projects/{project.id}/retry").status_code == 404
    asset = db.query(GeneratedAsset).filter_by(kind="video").one()
    assert other.get(f"/api/assets/{asset.id}/link").status_code == 404
    assert other.get("/api/admin/users").status_code == 403


def test_buttons_cannot_target_other_users_projects(client, db, fake_video):
    register(client)
    wa = link_whatsapp(client, db)
    say(client, db, wa, "make a funny 15 second youtube short about cats, cinematic, standard quality")
    btn = confirm_button(db, wa)

    bob = fresh_client()
    register(bob, email="bob@example.com", phone="+15551230000")
    bob_wa = link_whatsapp(bob, db, wa_id="15551230000")
    wa_button(bob, bob_wa, btn)  # Bob replays Asha's confirm button id
    run_pending(db)
    assert "expired" in last_reply(db, bob_wa).body
    assert db.query(VideoProject).one().status == "awaiting_confirmation"


def test_signed_download_urls(client, db, fake_video):
    register(client)
    wa = link_whatsapp(client, db)
    _make_project(client, db, wa, fake_video)
    asset = db.query(GeneratedAsset).filter_by(kind="video").one()
    url = sign_asset_url(asset.id).replace("http://testserver", "")
    anon = fresh_client()
    assert anon.get(url).status_code == 200
    tampered = url[:-3] + ("aaa" if not url.endswith("aaa") else "bbb")
    assert anon.get(tampered).status_code == 403
    other_asset = url.replace(asset.id, "00000000-0000-0000-0000-000000000000")
    assert anon.get(other_asset).status_code == 403
    expired = sign_asset_url(asset.id, ttl_seconds=-10).replace("http://testserver", "")
    r = anon.get(expired)
    assert r.status_code == 410 and "expired" in r.json()["detail"]


def test_admin_rbac_and_user_management(client, db, monkeypatch):
    from app.config import get_settings

    monkeypatch.setattr(get_settings(), "admin_email", "boss@example.com")
    register(client, email="boss@example.com", phone="+15551112222")
    user_client = fresh_client()
    register(user_client, email="u@example.com", phone="+15553334444")
    users = client.get("/api/admin/users").json()["items"]
    target = next(u for u in users if u["email"] == "u@example.com")
    assert client.patch(f"/api/admin/users/{target['id']}", json={"credits_delta": 5}).status_code == 200
    assert user_client.get("/api/me").json()["credits"] == 15
    assert client.patch(f"/api/admin/users/{target['id']}", json={"status": "suspended"}).status_code == 200
    assert user_client.get("/api/me").status_code == 403
    me = client.get("/api/me").json()
    assert client.patch(f"/api/admin/users/{me['id']}", json={"role": "user"}).status_code == 400
    actions = {a["action"] for a in client.get("/api/admin/audit-logs").json()["items"]}
    assert "admin.user_updated" in actions
    assert client.get("/api/admin/health").status_code == 200
    assert client.get("/api/admin/usage").json()["users"] == 2


def test_account_deletion_removes_data(client, db, fake_video):
    register(client)
    wa = link_whatsapp(client, db)
    _make_project(client, db, wa, fake_video)
    assert client.post("/api/me/delete", json={"password": "nope"}).status_code == 403
    assert client.post("/api/me/delete", json={"password": "correct horse battery"}).status_code == 200
    db.expire_all()
    assert db.query(User).count() == 0
    assert db.query(VideoProject).count() == 0
    assert db.query(GeneratedAsset).count() == 0


def test_login_rate_limited(client):
    register(client)
    codes = [client.post("/api/auth/login", json={"email": "x@example.com", "password": "nope"}).status_code
             for _ in range(12)]
    assert 429 in codes
