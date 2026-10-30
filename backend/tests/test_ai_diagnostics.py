from types import SimpleNamespace

import pytest
from sqlalchemy import select

from app import providers
from app.agents import diagnostics
from app.agents.base import AgentError
from app.agents.rules import RulesAgentProvider
from app.api.deps import current_user
from app.config import get_settings
from app.main import app
from app.models import UsageRecord, User, VideoPreferences, VideoProject
from app.pipeline import contracts as C
from app.pipeline.builtin.scoring import MomentScorer
from app.services import credits


@pytest.mark.parametrize("mode,expected", [("success", None), ("failed", "request_failed"),
                                           ("partial", "partial_vision"), ("rules", "ai_unavailable")])
def test_vision_diagnostics(mode, expected):
    def score(segments, frames, context):
        if mode == "failed":
            raise AgentError("429 RESOURCE_EXHAUSTED: quota exceeded")
        return [C.SegmentScore(segment_id=s.segment_id, score=8) for s in
                (segments[:1] if mode == "partial" else segments)]
    agent = SimpleNamespace(name="gemini", models={"default": "test-model"}, supports_vision=True,
                            score_moments=score)
    providers._overrides["agent"] = RulesAgentProvider() if mode == "rules" else agent
    segments = [C.Segment(segment_id=str(i), asset_id="a", kind="video", end=3) for i in range(2)]
    events = []
    token = diagnostics.ISSUE_SINK.set(events)
    try:
        result = MomentScorer().run(C.ScoringInput(segments=segments,
            context=C.EditContext(genre="travel", playbook={})))
    finally:
        diagnostics.ISSUE_SINK.reset(token)
    assert len(result.scores) == 2
    assert [e["diagnostic"]["code"] for e in events] == ([expected] if expected else [])
    if mode == "failed":
        assert "quota exceeded" in events[0]["diagnostic"]["error"]
    if mode in ("failed", "rules"):
        assert result.scorer == "heuristic"


def test_provider_initialization_reason_is_preserved(monkeypatch):
    def fail():
        raise ValueError("GEMINI_API_KEY is required for AI_PROVIDER=gemini")
    monkeypatch.setattr(providers, "build_agent", fail)
    agent = providers.get_agent()
    assert agent.name == "rules"
    assert "GEMINI_API_KEY is required" in diagnostics.unavailable(agent)
    monkeypatch.setattr(get_settings(), "ai_model_vision", "configured-vision-model")
    assert diagnostics.issue("vision", agent, diagnostics.unavailable(agent))["model"] == "configured-vision-model"


def test_sanitizes_keys_urls_and_bearer_tokens(monkeypatch):
    monkeypatch.setattr(get_settings(), "gemini_api_key", "private-test-key")
    result = diagnostics.safe_error("403 invalid private-test-key https://provider.test/?key=secret "
                                    "Bearer tokenvalue API_KEY=other-secret")
    assert "403 invalid" in result
    assert not any(secret in result for secret in ("private-test-key", "provider.test", "tokenvalue", "other-secret"))


def test_events_survive_rollback_and_do_not_count_as_model_calls(db, client):
    admin, owner = User(name="Admin", role="admin"), User(name="Owner")
    db.add_all([admin, owner]); db.flush()
    project = VideoProject(user_id=owner.id, status="completed")
    db.add(project); db.commit()
    event = diagnostics.issue("vision", SimpleNamespace(name="gemini", models={"vision": "test-model"}),
                              "429 quota exceeded")
    for _ in range(2):
        with credits.persist_usage(db):
            credits.record_ai_issue(db, event, user_id=owner.id, project_id=project.id, revision=1)
            db.rollback()
    app.dependency_overrides[current_user] = lambda: admin
    try:
        listing = client.get("/api/admin/users").json()
        assert next(u for u in listing["items"] if u["id"] == owner.id)["ai_issue_count"] == 2
        usage = client.get(f"/api/admin/users/{owner.id}/usage").json()
        assert usage["summary"]["tokens"] == usage["summary"]["llm_calls"] == 0
        assert usage["items"][0]["ai_issue_count"] == 2
        url = f"/api/admin/users/{owner.id}/projects/{project.id}/ai-issues"
        logs = client.get(url + "?limit=1").json()
        assert logs["total"] == 2 and len(logs["items"]) == 1
        assert logs["items"][0]["error"] == "429 quota exceeded"
        assert client.get(url + "?limit=1&offset=1").json()["items"][0]["id"] != logs["items"][0]["id"]
        assert client.get(f"/api/admin/users/{admin.id}/projects/{project.id}/ai-issues").status_code == 404
        app.dependency_overrides[current_user] = lambda: owner
        assert client.get(url).status_code == 403
        app.dependency_overrides.pop(current_user)
        assert client.get(url).status_code == 401
    finally:
        app.dependency_overrides.pop(current_user, None)


def test_historical_plan_evidence_and_zero_usage_are_distinct(db, client):
    admin = User(name="Admin", role="admin")
    db.add(admin); db.flush()
    legacy, unknown = VideoProject(user_id=admin.id), VideoProject(user_id=admin.id)
    db.add_all([legacy, unknown]); db.flush()
    db.add(VideoPreferences(project_id=legacy.id, revision=1, plan={"scorer": "heuristic"}))
    db.commit()
    app.dependency_overrides[current_user] = lambda: admin
    try:
        projects = {p["id"]: p for p in client.get(f"/api/admin/users/{admin.id}/usage").json()["items"]}
        assert projects[legacy.id]["ai_issue_count"] == 1
        assert projects[unknown.id]["ai_issue_count"] == 0
        logs = client.get(f"/api/admin/users/{admin.id}/projects/{legacy.id}/ai-issues").json()
        assert logs["historical"][0]["historical"] is True
        assert "original cause was not recorded" in logs["historical"][0]["error"]
        # A recorded vision event replaces the inferred historical warning.
        credits.record_ai_issue(db, diagnostics.issue("vision", RulesAgentProvider(), "AI disabled"),
                                user_id=admin.id, project_id=legacy.id, revision=1)
        db.commit()
        logs = client.get(f"/api/admin/users/{admin.id}/projects/{legacy.id}/ai-issues").json()
        assert logs["total"] == 1 and logs["historical"] == []
    finally:
        app.dependency_overrides.pop(current_user, None)


@pytest.mark.parametrize("failed", [False, True])
def test_analysis_fallback_is_linked_to_initiating_project(db, failed):
    from tests.test_engine import A, Scripted, send
    from app.models import Conversation, WhatsAppAccount, utcnow
    owner = User(name="Owner")
    db.add(owner); db.flush()
    wa = WhatsAppAccount(wa_id="15550006789", user_id=owner.id, notice_sent_at=utcnow())
    db.add(wa); db.flush()
    conv = Conversation(whatsapp_account_id=wa.id, user_id=owner.id)
    db.add(conv); db.commit()
    agent = Scripted()
    providers._overrides["agent"] = agent
    _, msg = send(db, conv, "make a reel", analysis=AgentError("AI analysis timed out") if failed else A("create_reel"), agent=agent)
    event = db.scalars(select(UsageRecord).where(UsageRecord.kind == "ai_fallback")).one()
    assert event.project_id == msg.project_id and event.project_id is not None
    assert event.message_id == msg.id and event.revision == msg.revision == 1
    if failed:
        assert event.diagnostic["error"] == "AI analysis timed out"


def test_pipeline_failure_keeps_diagnostics_and_resets_scope(db, monkeypatch):
    from app.pipeline import run
    from app.jobs.tasks import plan_project
    owner = User(name="Owner")
    db.add(owner); db.flush()
    project = VideoProject(user_id=owner.id, status="awaiting_preferences", stage="planning")
    db.add(project); db.flush()
    db.add(VideoPreferences(project_id=project.id, revision=1)); db.commit()
    monkeypatch.setattr(run, "source_assets", lambda *args: [C.SourceAsset(
        asset_id="a", kind="video", order_index=0, file_path="unused.mp4", duration=4, width=720, height=1280)])
    def fail(*args):
        diagnostics.report("vision", SimpleNamespace(name="gemini", models={}), "503 provider unavailable")
        raise ValueError("later processing failed")
    monkeypatch.setattr(run, "run_step", fail)
    plan_project(project.id, 1)
    db.expire_all()
    event = db.scalars(select(UsageRecord).where(UsageRecord.kind == "ai_fallback")).one()
    assert event.project_id == project.id and event.revision == 1
    assert event.diagnostic["error"] == "503 provider unavailable"
    assert diagnostics.ISSUE_SINK.get() is None


def test_script_failure_records_error_before_using_template():
    from app.pipeline.builtin.script import ScriptWriter
    def fail(*args):
        raise AgentError("provider returned malformed script")
    providers._overrides["agent"] = SimpleNamespace(name="gemini", models={}, is_llm=True, write_script=fail)
    events = []
    token = diagnostics.ISSUE_SINK.set(events)
    try:
        result = ScriptWriter().run(C.ScriptInput(items=[C.PlanItem(
            segment_id="s", asset_id="a", kind="video", source_start=0, source_end=3, role="hook")],
            total_seconds=3, context=C.EditContext(genre="travel", playbook={})))
    finally:
        diagnostics.ISSUE_SINK.reset(token)
    assert result.title
    assert events[0]["task"] == "script"
    assert events[0]["diagnostic"]["error"] == "provider returned malformed script"
