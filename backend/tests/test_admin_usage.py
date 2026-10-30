"""Admin usage must reconcile without exposing any user-created content."""
import pytest
from sqlalchemy import select

from app.api.deps import current_user
from app.main import app
from app.models import Conversation, GeneratedAsset, GenerationJob, Message, UsageRecord, User, VideoProject, WhatsAppAccount
from app.agents.base import UsageInfo, USAGE_SINK
from app.services import credits


@pytest.fixture
def usage_world(db):
    admin, owner, other = User(name="Admin", role="admin"), User(name="Owner"), User(name="Other")
    db.add_all([admin, owner, other]); db.flush()
    wa = WhatsAppAccount(wa_id="15550001234", user_id=owner.id)
    db.add(wa); db.flush()
    conv = Conversation(whatsapp_account_id=wa.id, user_id=owner.id)
    db.add(conv); db.flush()
    p = VideoProject(user_id=owner.id, revision=2, title="PRIVATE_TITLE", status="completed")
    db.add(p); db.flush()
    asset = GeneratedAsset(user_id=owner.id, project_id=p.id, kind="video", storage_key="PRIVATE_STORAGE",
                           mime_type="video/mp4", extra={"script": "PRIVATE_SCRIPT"})
    db.add(asset)
    for direction in ["inbound", "inbound", "outbound"]:
        db.add(Message(user_id=owner.id, conversation_id=conv.id, project_id=p.id, revision=1,
                       direction=direction, kind="text", body="PRIVATE_MESSAGE", transcript="PRIVATE_TRANSCRIPT", status="processed"))
    db.add(GenerationJob(user_id=owner.id, project_id=p.id, revision=2, attempts=3,
                         idempotency_key="job", error_message="PRIVATE_ERROR"))
    for rev, inp, out in [(1, 100, 10), (2, 200, 20), (2, 50, 5)]:
        credits.record_llm(db, UsageInfo("openai", "gpt-4.1-mini", inp, out, "script"),
                           user_id=owner.id, project_id=p.id, revision=rev)
    credits.record_llm(db, UsageInfo("gemini", "unknown", 7, 3, "analysis"), user_id=owner.id)
    credits.record_llm(db, UsageInfo("openai", "gpt-4.1-mini", 999, 1), user_id=other.id)
    db.commit()
    app.dependency_overrides[current_user] = lambda: admin
    yield admin, owner, other, p, asset
    app.dependency_overrides.pop(current_user, None)


def test_usage_reconciles_and_excludes_private_content(client, db, usage_world):
    admin, owner, _, p, asset = usage_world
    response = client.get(f"/api/admin/users/{owner.id}/usage")
    assert response.status_code == 200
    data = response.json()
    assert data["summary"]["tokens"] == 395
    assert data["summary"]["messages"] == 2
    assert data["unattributed"]["tokens"] == 10
    assert data["unattributed"]["cost_complete"] is False
    project = data["items"][0]
    assert project["tokens"] == 385 and project["messages"] == 2
    assert [r["tokens"] for r in project["revisions"]] == [110, 275]
    assert project["revisions"][1]["attempts"] == 3
    assert project["revisions"][1]["tasks"][0]["llm_calls"] == 2
    assert "PRIVATE_" not in response.text
    forbidden = {"title", "body", "transcript", "prompt", "script", "url", "storage_key", "asset_id", "payload", "error_message"}
    def check(value):
        if isinstance(value, dict):
            assert not forbidden.intersection(value)
            for item in value.values(): check(item)
        elif isinstance(value, list):
            for item in value: check(item)
    check(data)
    assert client.get(f"/api/projects/{p.id}").status_code == 404
    assert client.get(f"/api/assets/{asset.id}/link").status_code == 404


def test_usage_auth_missing_user_and_pagination(client, db, usage_world):
    _, owner, other, _, _ = usage_world
    app.dependency_overrides[current_user] = lambda: other
    assert client.get(f"/api/admin/users/{owner.id}/usage").status_code == 403
    app.dependency_overrides.pop(current_user)
    assert client.get(f"/api/admin/users/{owner.id}/usage").status_code == 401
    app.dependency_overrides[current_user] = lambda: usage_world[0]
    assert client.get("/api/admin/users/missing/usage").status_code == 404
    assert client.get(f"/api/admin/users/{owner.id}/usage?limit=0").status_code == 422
    page = client.get(f"/api/admin/users/{owner.id}/usage?limit=1&offset=1").json()
    assert page["items"] == [] and page["total"] == 1 and page["summary"]["tokens"] == 395


def test_provider_usage_survives_rollback_and_counts_actual_retries(db):
    owner = User(name="Owner")
    db.add(owner); db.commit()
    for _ in range(2):
        with pytest.raises(ValueError), credits.persist_usage(db):
            credits.record_llm(db, UsageInfo("openai", "gpt-4.1-mini", 1, 1), user_id=owner.id)
            db.rollback()
            raise ValueError("later step failed")
    records = db.scalars(select(UsageRecord)).all()
    assert len(records) == 2 and sum(r.quantity for r in records) == 4
    assert all(float(r.estimated_cost_usd) == pytest.approx(0.000002) for r in records)


def test_initiating_message_and_revision_attribution(db):
    from tests.test_engine import A, Scripted, send
    from app import providers
    from app.models import utcnow
    owner = User(name="Owner")
    db.add(owner); db.flush()
    wa = WhatsAppAccount(wa_id="15550005678", user_id=owner.id, notice_sent_at=utcnow())
    db.add(wa); db.flush()
    conv = Conversation(whatsapp_account_id=wa.id, user_id=owner.id)
    db.add(conv); db.commit()
    agent = Scripted()
    agent.usage = [UsageInfo("openai", "gpt-4.1-mini", 10, 2, "analysis")]
    providers._overrides["agent"] = agent
    _, msg = send(db, conv, "make a reel", analysis=A("create_reel"), agent=agent)
    record = db.scalars(select(UsageRecord).where(UsageRecord.kind == "llm")).one()
    assert msg.project_id is not None and record.project_id == msg.project_id
    assert msg.revision == record.revision == 1 and record.message_id == msg.id
    project = db.get(VideoProject, msg.project_id)
    project.status, project.revision = "completed", 1
    db.add(GenerationJob(user_id=owner.id, project_id=project.id, revision=1, status="completed", idempotency_key="done"))
    db.commit()
    _, changed = send(db, conv, "shorter", analysis=A("modify", fields={"duration": "15"}), agent=agent)
    assert changed.project_id == project.id and changed.revision == 2


def test_pipeline_failure_keeps_usage(db, monkeypatch):
    from app.pipeline import run
    from app.models import VideoPreferences
    from app.jobs.tasks import plan_project
    owner = User(name="Owner")
    db.add(owner); db.flush()
    p = VideoProject(user_id=owner.id, status="awaiting_preferences", stage="planning")
    db.add(p); db.flush()
    db.add(VideoPreferences(project_id=p.id, revision=1)); db.commit()
    from app.pipeline.contracts import SourceAsset
    monkeypatch.setattr(run, "source_assets", lambda *args: [SourceAsset(
        asset_id="asset", kind="video", file_path="unused.mp4", order_index=0, duration=4, width=720, height=1280)])
    def fail(*args):
        USAGE_SINK.get().append(UsageInfo("openai", "gpt-4.1-mini", 12, 3, "scoring"))
        raise ValueError("pipeline failed after provider call")
    monkeypatch.setattr(run, "run_step", fail)
    plan_project(p.id, 1)
    db.expire_all()
    assert p.last_error == "pipeline failed after provider call", p.last_error
    row = db.scalars(select(UsageRecord).where(UsageRecord.kind == "llm")).one()
    assert row.project_id == p.id and row.revision == 1 and row.quantity == 15


def test_usage_migration_backfills_only_job_links(tmp_path, monkeypatch):
    monkeypatch.setattr("logging.config.fileConfig", lambda *args, **kwargs: None)
    from alembic import command
    from alembic.config import Config
    from sqlalchemy import create_engine, text, inspect
    from app.config import get_settings
    monkeypatch.setattr(get_settings(), "database_url", f"sqlite:///{tmp_path / 'migration.db'}")
    config = Config("alembic.ini")
    command.upgrade(config, "0002")
    engine = create_engine(get_settings().database_url)
    with engine.begin() as conn:
        conn.execute(text("""INSERT INTO users (id,name,is_anonymous,role,status,credits_balance,created_at,updated_at)
            VALUES ('u','User',0,'user','active',0,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)"""))
        conn.execute(text("""INSERT INTO video_projects (id,user_id,title,status,revision,created_at,updated_at)
            VALUES ('p','u','PRIVATE','completed',2,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)"""))
        conn.execute(text("""INSERT INTO generation_jobs (id,project_id,user_id,revision,idempotency_key,status,attempts,run_number,credits_charged,estimated_cost_usd,created_at)
            VALUES ('j','p','u',2,'key','completed',1,1,0,0,CURRENT_TIMESTAMP)"""))
        for rid, job in [("linked", "j"), ("unknown", None)]:
            conn.execute(text("""INSERT INTO usage_records (id,user_id,job_id,kind,credits_delta,quantity,unit,estimated_cost_usd,created_at)
                VALUES (:id,'u',:job,'llm',0,10,'tokens',0,CURRENT_TIMESTAMP)"""), {"id": rid, "job": job})
    command.upgrade(config, "head")
    with engine.connect() as conn:
        rows = {r.id: r for r in conn.execute(text("SELECT id,project_id,revision,input_tokens FROM usage_records"))}
        assert rows["linked"].project_id == "p" and rows["linked"].revision == 2
        assert rows["unknown"].project_id is None and rows["linked"].input_tokens is None
    command.downgrade(config, "0002")
    assert "project_id" not in {c["name"] for c in inspect(engine).get_columns("usage_records")}
    engine.dispose()
