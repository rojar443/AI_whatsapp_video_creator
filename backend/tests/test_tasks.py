"""Background tasks in isolation: guard clauses, failure paths, final-failure hooks, sweep and retention."""
from datetime import timedelta

import pytest

from app import providers
from app.jobs import tasks
from app.jobs.dispatch import INLINE, REGISTRY, RetryableError
from app.media.ffmpeg import FFmpegError
from app.media.intake import MediaInfo, MediaRejected
from app.messaging.base import SendError
from app.models import (
    Conversation,
    GeneratedAsset,
    GenerationJob,
    LoginToken,
    Message,
    ProjectAsset,
    User,
    VideoPreferences,
    VideoProject,
    WhatsAppAccount,
    utcnow,
)
from app.pipeline.registry import StepError
from app.pipeline.run import RenderFailure
from app.storage import get_storage

S = tasks.S


@pytest.fixture
def world(db):
    """User + WhatsApp account + conversation + project in collecting_media."""
    user = User(name="Asha", credits_balance=5)
    db.add(user)
    db.flush()
    wa = WhatsAppAccount(wa_id="15550001111", user_id=user.id, status="linked")
    db.add(wa)
    db.flush()
    conv = Conversation(whatsapp_account_id=wa.id, user_id=user.id)
    db.add(conv)
    db.flush()
    p = VideoProject(user_id=user.id, conversation_id=conv.id, last_media_at=utcnow())
    db.add(p)
    db.flush()
    conv.active_project_id = p.id
    db.commit()
    return user, conv, p


def out_texts(db, conv):
    db.expire_all()
    return [m.body or "" for m in db.query(Message).filter_by(conversation_id=conv.id, direction="outbound")
            .order_by(Message.created_at)]


def add_asset(db, p, **kw):
    a = ProjectAsset(project_id=p.id, user_id=p.user_id, kind=kw.pop("kind", "video"),
                     media_id=kw.pop("media_id", f"m{len(kw)}{utcnow().timestamp()}"), mime_type="video/mp4", **kw)
    db.add(a)
    db.commit()
    return a


def add_job(db, p, **kw):
    j = GenerationJob(project_id=p.id, user_id=p.user_id, revision=p.revision,
                      idempotency_key=f"{p.id}:r{p.revision}:{kw.pop('n', 1)}", **kw)
    db.add(j)
    db.commit()
    return j


def queued(name=None):
    return [t for t in INLINE.ready + INLINE.delayed if name is None or t[0] == name]


# ------------------------------------------------------------------ hooks
def test_final_failure_hooks_registered():
    assert REGISTRY["download_asset"].on_final_failure is tasks.download_failed
    assert REGISTRY["plan_project"].on_final_failure is tasks.plan_final_failure
    assert REGISTRY["render_job"].on_final_failure is tasks.render_final_failure
    assert REGISTRY["deliver"].on_final_failure is tasks.deliver_final_failure
    assert REGISTRY["download_asset"].max_retries == 3 and REGISTRY["plan_project"].queue == "media"


def test_outbox_none_without_conversation(db, world):
    _, _, p = world
    p.conversation_id = None
    assert tasks._outbox(db, p) is None


# ------------------------------------------------------------------ download_asset
class FakeMessaging:
    def __init__(self, exc=None, mime="video/mp4", data=b"\x00" * 100):
        self.exc, self.mime, self.data, self.calls = exc, mime, data, []

    def download_media(self, media_id, dest, limit):
        self.calls.append((media_id, limit))
        if self.exc:
            raise self.exc
        open(dest, "wb").write(self.data)
        return self.mime


def test_download_asset_skips_missing_and_non_pending(db, world):
    _, _, p = world
    providers._overrides["messaging"] = m = FakeMessaging()
    tasks.download_asset("nope")
    a = add_asset(db, p, status="ready")
    tasks.download_asset(a.id)
    assert m.calls == []


def test_download_asset_success(db, world, monkeypatch):
    _, _, p = world
    providers._overrides["messaging"] = FakeMessaging(mime="")
    monkeypatch.setattr(tasks, "inspect", lambda path, mime, size: MediaInfo("video", "mp4", 4.0, 1080, 1920, "h264", True))
    a = add_asset(db, p)
    tasks.download_asset(a.id)
    db.expire_all()
    a = db.get(ProjectAsset, a.id)
    assert a.status == "ready" and a.size_bytes == 100 and a.mime_type == "video/mp4"
    assert a.storage_key == f"users/{p.user_id}/projects/{p.id}/src/{a.id}.mp4"
    assert (a.duration_seconds, a.width, a.height, a.codec, a.has_audio) == (4.0, 1080, 1920, "h264", True)
    assert get_storage().exists(a.storage_key)


def test_download_asset_uses_image_limit(db, world, monkeypatch):
    from app.config import get_settings

    _, _, p = world
    providers._overrides["messaging"] = m = FakeMessaging()
    monkeypatch.setattr(tasks, "inspect", lambda *a: (_ for _ in ()).throw(MediaRejected("x")))
    tasks.download_asset(add_asset(db, p, kind="photo").id)
    assert m.calls[0][1] == get_settings().max_image_bytes


def test_download_asset_retryable_error(db, world):
    _, _, p = world
    providers._overrides["messaging"] = FakeMessaging(exc=SendError("503", retryable=True))
    a = add_asset(db, p)
    with pytest.raises(RetryableError):
        tasks.download_asset(a.id)


@pytest.mark.parametrize("code,status,reason,msg", [
    ("too_large", "rejected", "file is too large", "couldn't use"),
    (None, "failed", "download failed", "couldn't download"),
])
def test_download_asset_permanent_error(db, world, code, status, reason, msg):
    _, conv, p = world
    providers._overrides["messaging"] = FakeMessaging(exc=SendError("x", retryable=False, code=code))
    a = add_asset(db, p)
    tasks.download_asset(a.id)
    db.expire_all()
    a = db.get(ProjectAsset, a.id)
    assert a.status == status and a.reject_reason == reason
    assert msg in out_texts(db, conv)[-1]


def test_download_asset_media_rejected(db, world, monkeypatch):
    _, conv, p = world
    providers._overrides["messaging"] = FakeMessaging()
    monkeypatch.setattr(tasks, "inspect", lambda *a: (_ for _ in ()).throw(MediaRejected("unsupported file type")))
    a = add_asset(db, p)
    tasks.download_asset(a.id)
    db.expire_all()
    assert db.get(ProjectAsset, a.id).status == "rejected"
    assert "unsupported file type" in out_texts(db, conv)[-1]


def test_reject_without_conversation_still_saves(db, world):
    _, conv, p = world
    p.conversation_id = None
    db.commit()
    a = add_asset(db, p)
    tasks._reject(db, a, p, "bad")
    db.expire_all()
    assert db.get(ProjectAsset, a.id).status == "rejected" and out_texts(db, conv) == []


def test_download_failed_hook(db, world):
    _, conv, p = world
    a = add_asset(db, p)
    done = add_asset(db, p, status="ready")
    tasks.download_failed(a.id, error="boom")
    tasks.download_failed(done.id)
    tasks.download_failed("missing")
    db.expire_all()
    assert db.get(ProjectAsset, a.id).status == "failed" and db.get(ProjectAsset, done.id).status == "ready"
    assert len(out_texts(db, conv)) == 1


# ------------------------------------------------------------------ batch_ack / batch_timeout
def test_batch_ack_guards(db, world):
    _, conv, p = world
    tasks.batch_ack("missing", 1)
    add_asset(db, p)
    tasks.batch_ack(p.id, 2)  # more/less media than expected → stale
    p.status = S.processing.value
    db.commit()
    tasks.batch_ack(p.id, 1)
    assert out_texts(db, conv) == []


@pytest.mark.parametrize("prefs,expected", [(False, "tell me what you want"), (True, "that's all")])
def test_batch_ack_message_depends_on_prefs(db, world, prefs, expected):
    _, conv, p = world
    add_asset(db, p)
    if prefs:
        db.add(VideoPreferences(project_id=p.id, revision=p.revision, genre="travel"))
        db.commit()
    tasks.batch_ack(p.id, 1)
    assert expected in out_texts(db, conv)[-1]


def test_batch_ack_no_conversation(db, world):
    _, conv, p = world
    add_asset(db, p)
    p.conversation_id = None
    db.commit()
    tasks.batch_ack(p.id, 1)
    assert out_texts(db, conv) == []


def test_batch_timeout_guards(db, world, monkeypatch):
    _, _, p = world
    from app.conversation import engine

    called = []
    monkeypatch.setattr(engine.ConversationEngine, "close_batch", lambda self, proj, out: called.append(proj.id))
    tasks.batch_timeout("missing", utcnow().isoformat())
    tasks.batch_timeout(p.id, (p.last_media_at - timedelta(seconds=5)).isoformat())  # newer media arrived
    p.conversation_id = None
    db.commit()
    tasks.batch_timeout(p.id, p.last_media_at.isoformat())
    assert called == []
    p.conversation_id = world[1].id
    p.last_media_at = None
    db.commit()
    tasks.batch_timeout(p.id, utcnow().isoformat())
    assert called == []


def test_batch_timeout_closes_batch(db, world, monkeypatch):
    _, _, p = world
    from app.conversation import engine

    called = []
    monkeypatch.setattr(engine.ConversationEngine, "close_batch", lambda self, proj, out: called.append(proj.id))
    db.refresh(p)
    tasks.batch_timeout(p.id, p.last_media_at.isoformat())
    assert called == [p.id]


# ------------------------------------------------------------------ plan_project
@pytest.fixture
def planning(db, world):
    user, conv, p = world
    p.status, p.stage = S.awaiting_preferences.value, "planning"
    db.add(VideoPreferences(project_id=p.id, revision=p.revision, genre="travel"))
    db.commit()
    return user, conv, p


def test_plan_project_guards(db, planning, monkeypatch):
    _, _, p = planning
    from app.pipeline import run

    monkeypatch.setattr(run, "build_plan", lambda *a: pytest.fail("should not plan"))
    tasks.plan_project("missing", 1)
    tasks.plan_project(p.id, p.revision + 1)
    p.stage = None
    db.commit()
    tasks.plan_project(p.id, p.revision)


def test_plan_project_waits_for_pending_downloads(db, planning, monkeypatch):
    _, _, p = planning
    from app.pipeline import run

    monkeypatch.setattr(run, "build_plan", lambda *a: pytest.fail("should wait"))
    add_asset(db, p)
    tasks.plan_project(p.id, p.revision)
    assert queued("plan_project") == [("plan_project", [p.id, p.revision], 1)]


def test_plan_project_stops_waiting_after_5_minutes(db, planning, monkeypatch):
    _, _, p = planning
    from app.pipeline import run

    seen = []
    monkeypatch.setattr(run, "build_plan", lambda db_, proj, row: seen.append(1) or (_ for _ in ()).throw(ValueError("no usable clips")))
    add_asset(db, p)
    p.last_media_at = utcnow() - timedelta(minutes=6)
    db.commit()
    tasks.plan_project(p.id, p.revision)
    assert seen == [1]


@pytest.mark.parametrize("exc", [StepError("bad input"), ValueError("no usable clips")])
def test_plan_project_permanent_failure(db, planning, monkeypatch, exc):
    _, conv, p = planning
    from app.pipeline import run

    monkeypatch.setattr(run, "build_plan", lambda *a: (_ for _ in ()).throw(exc))
    tasks.plan_project(p.id, p.revision)
    db.expire_all()
    p = db.get(VideoProject, p.id)
    assert p.stage == "plan_failed" and p.last_error == str(exc)
    assert "couldn't put a plan together" in out_texts(db, conv)[-1]


def test_plan_project_retryable_failure(db, planning, monkeypatch):
    _, _, p = planning
    from app.pipeline import run

    monkeypatch.setattr(run, "build_plan", lambda *a: (_ for _ in ()).throw(StepError("timeout", retryable=True)))
    with pytest.raises(RetryableError):
        tasks.plan_project(p.id, p.revision)


def test_plan_project_success_presents_plan(db, planning, monkeypatch):
    _, _, p = planning
    from app.conversation import engine
    from app.pipeline import run

    monkeypatch.setattr(run, "build_plan", lambda *a: {"items": [1, 2], "playbook": "travel@3"})
    shown = []
    monkeypatch.setattr(engine.ConversationEngine, "present_plan", lambda self, proj, out: shown.append(proj.id))
    tasks.plan_project(p.id, p.revision)
    db.expire_all()
    row = db.query(VideoPreferences).filter_by(project_id=p.id).one()
    assert row.plan["items"] == [1, 2] and row.playbook_version == "travel@3" and shown == [p.id]


def test_plan_project_success_without_conversation(db, planning, monkeypatch):
    _, _, p = planning
    from app.pipeline import run

    monkeypatch.setattr(run, "build_plan", lambda *a: {"items": [], "playbook": None})
    p.conversation_id = None
    db.commit()
    tasks.plan_project(p.id, p.revision)
    db.expire_all()
    p = db.get(VideoProject, p.id)
    assert p.status == S.awaiting_confirmation.value and p.stage is None


def test_plan_final_failure(db, planning):
    _, conv, p = planning
    tasks.plan_final_failure(p.id, p.revision, error="x")
    tasks.plan_final_failure("missing", 1)
    db.expire_all()
    assert db.get(VideoProject, p.id).last_error == "a processing service is unavailable"
    tasks.plan_final_failure(p.id, p.revision)  # stage no longer "planning" → no second message
    assert len(out_texts(db, conv)) == 1


def test_plan_failed_without_conversation(db, planning):
    _, conv, p = planning
    p.conversation_id = None
    tasks._plan_failed(db, p, "x" * 600)
    assert len(p.last_error) == 500 and out_texts(db, conv) == []


# ------------------------------------------------------------------ render_job / fail_job
def test_render_job_skips_finished(db, world, monkeypatch):
    _, _, p = world
    from app.pipeline import run

    monkeypatch.setattr(run, "render_job", lambda *a: pytest.fail("should skip"))
    tasks.render_job("missing")
    for st in ("completed", "cancelled", "failed"):
        j = add_job(db, p, status=st, n=st)
        tasks.render_job(j.id)


@pytest.mark.parametrize("exc,code", [
    (StepError("bad", code="invalid_output"), "invalid_output"),
    (RenderFailure("no clips", code="no_media"), "no_media"),
    (FFmpegError("x"), "render_failed"),
])
def test_render_job_permanent_failures(db, world, monkeypatch, exc, code):
    user, conv, p = world
    from app.pipeline import run

    monkeypatch.setattr(run, "render_job", lambda *a: (_ for _ in ()).throw(exc))
    j = add_job(db, p, credits_charged=2)
    tasks.render_job(j.id)
    db.expire_all()
    j = db.get(GenerationJob, j.id)
    assert j.status == "failed" and j.error_code == code and j.attempts == 1
    assert db.get(VideoProject, p.id).status == S.failed.value
    assert db.get(User, user.id).credits_balance == 7  # refunded
    texts = out_texts(db, conv)
    assert "Working on" in texts[0] or len(texts) == 2
    assert "refunded" in texts[-1]


def test_render_job_retryable_and_no_second_processing_message(db, world, monkeypatch):
    _, conv, p = world
    from app.pipeline import run

    monkeypatch.setattr(run, "render_job", lambda *a: (_ for _ in ()).throw(StepError("busy", retryable=True)))
    j = add_job(db, p)
    with pytest.raises(RetryableError, match="step_failed: busy"):
        tasks.render_job(j.id)
    n = len(out_texts(db, conv))
    with pytest.raises(RetryableError):
        tasks.render_job(j.id)
    assert len(out_texts(db, conv)) == n  # "processing" only on first attempt
    assert db.get(GenerationJob, j.id).attempts == 2


def render_result(db, user, demo=()):
    a = GeneratedAsset(user_id=user.id, kind="video", storage_key="r.mp4", mime_type="video/mp4", duration_seconds=12)
    db.add(a)
    db.flush()
    return {"title": "Goa days", "asset": a, "demo": list(demo)}


def test_render_job_success_anonymous_gets_nudge_and_demo_notice(db, world, monkeypatch):
    user, conv, p = world
    from app.pipeline import run

    user.is_anonymous = True
    db.commit()
    stages = []

    def fake(db_, job, on_stage):
        on_stage("captions")
        stages.append(job.stage)
        return render_result(db_, user, demo=["voice"])

    monkeypatch.setattr(run, "render_job", fake)
    j = add_job(db, p)
    tasks.render_job(j.id)
    db.expire_all()
    j = db.get(GenerationJob, j.id)
    assert j.status == "completed" and j.stage == "done" and stages == ["captions"]
    p = db.get(VideoProject, p.id)
    assert p.status == S.completed.value and p.title == "Goa days"
    texts = " | ".join(out_texts(db, conv))
    assert "voice" in texts and "%2B15550001111" in texts


def test_render_job_success_registered_user_no_nudge(db, world, monkeypatch):
    user, conv, p = world
    from app.pipeline import run

    monkeypatch.setattr(run, "render_job", lambda db_, job, cb: {**render_result(db_, user), "title": None})
    j = add_job(db, p)
    tasks.render_job(j.id)
    db.expire_all()
    assert db.get(VideoProject, p.id).title == "New reel"
    assert "%2B" not in " ".join(out_texts(db, conv))


def test_render_job_without_conversation(db, world, monkeypatch):
    user, conv, p = world
    from app.pipeline import run

    p.conversation_id = None
    db.commit()
    monkeypatch.setattr(run, "render_job", lambda db_, job, cb: render_result(db_, user))
    tasks.render_job(add_job(db, p).id)
    assert out_texts(db, conv) == []


def test_fail_job_idempotent(db, world):
    _, conv, p = world
    j = add_job(db, p, credits_charged=1)
    tasks.fail_job(j.id, "narration_failed", "tts down")
    tasks.fail_job(j.id, "x", "again")
    tasks.fail_job("missing", "x", "y")
    texts = out_texts(db, conv)
    assert len(texts) == 1 and "voiceover failed" in texts[0]


def test_fail_job_without_conversation(db, world):
    _, conv, p = world
    p.conversation_id = None
    db.commit()
    tasks.fail_job(add_job(db, p).id, "x", "y")
    assert out_texts(db, conv) == []


@pytest.mark.parametrize("error,code", [("agent down", "agent_unreachable"), ("ffmpeg", "render_failed"),
                                        ("", "render_failed")])
def test_render_final_failure(db, world, error, code):
    _, _, p = world
    j = add_job(db, p)
    tasks.render_final_failure(j.id, error=error)
    db.expire_all()
    j = db.get(GenerationJob, j.id)
    assert j.error_code == code and j.error_message == (error or "retries exhausted")


# ------------------------------------------------------------------ deliver
def test_deliver_final_failure(db, world):
    _, conv, _ = world
    pend = Message(conversation_id=conv.id, direction="outbound", kind="text", status="pending")
    sent = Message(conversation_id=conv.id, direction="outbound", kind="text", status="sent")
    db.add_all([pend, sent])
    db.commit()
    tasks.deliver_final_failure([pend.id, sent.id, "missing"])
    db.expire_all()
    assert db.get(Message, pend.id).status == "failed" and db.get(Message, pend.id).error == "delivery failed"
    assert db.get(Message, sent.id).status == "sent"


def test_deliver_uses_messaging(monkeypatch):
    seen = []
    monkeypatch.setattr(tasks, "deliver_batch", lambda db, ids, m: seen.append(ids))
    tasks.deliver(["a"])
    assert seen == [["a"]]


# ------------------------------------------------------------------ sweep
def test_sweep_requeues_stale_work(db, world, monkeypatch):
    from app.config import get_settings

    user, conv, p = world
    old = utcnow() - timedelta(hours=2)
    stale_in = Message(conversation_id=conv.id, direction="inbound", kind="text", status="received", created_at=old)
    fresh_in = Message(conversation_id=conv.id, direction="inbound", kind="text", status="received")
    stale_out = Message(conversation_id=conv.id, direction="outbound", kind="text", status="pending", created_at=old)
    db.add_all([stale_in, fresh_in, stale_out])
    stale_asset = add_asset(db, p, created_at=old)
    add_asset(db, p)
    q = add_job(db, p, created_at=old, n=1)
    p.last_media_at = old
    p2 = VideoProject(user_id=user.id, conversation_id=conv.id, stage="planning", status=S.awaiting_preferences.value,
                      updated_at=old)
    db.add(p2)
    db.commit()
    # updated_at has onupdate; force the old value
    p3 = VideoProject(user_id=user.id, status=S.processing.value)
    db.add(p3)
    db.commit()
    stuck = add_job(db, p3, status="processing", started_at=old, n=2)
    db.query(VideoProject).filter_by(id=p2.id).update({"updated_at": old}, synchronize_session=False)
    db.commit()
    tasks.sweep()
    names = {(t[0], tuple(t[1]) if t[0] != "deliver" else tuple(t[1][0])) for t in queued()}
    assert ("process_inbound", (stale_in.id,)) in names
    assert ("process_inbound", (fresh_in.id,)) not in names
    assert ("deliver", (stale_out.id,)) in names
    assert ("download_asset", (stale_asset.id,)) in names
    assert ("render_job", (q.id,)) in names
    assert any(t[0] == "batch_timeout" and t[1][0] == p.id for t in queued())
    assert ("plan_project", (p2.id, p2.revision)) in names
    db.expire_all()
    assert db.get(GenerationJob, stuck.id).error_code == "timeout"


def test_sweep_nothing_to_do(db, world):
    tasks.sweep()
    assert queued() == []


# ------------------------------------------------------------------ purge_expired
def test_purge_expired(db, world, monkeypatch):
    from app.config import get_settings

    user, conv, p = world
    st = get_storage()
    now = utcnow()
    st.put_bytes("reels/old.mp4", b"x")
    st.put_bytes("src/old.mp4", b"x")
    expired = GeneratedAsset(user_id=user.id, kind="video", storage_key="reels/old.mp4", mime_type="video/mp4",
                             expires_at=now - timedelta(hours=1))
    keep = GeneratedAsset(user_id=user.id, kind="video", storage_key="reels/new.mp4", mime_type="video/mp4",
                          expires_at=now + timedelta(hours=1))
    db.add_all([expired, keep])
    old_src = add_asset(db, p, status="ready", storage_key="src/old.mp4",
                        created_at=now - timedelta(days=get_settings().source_media_retention_days + 1))
    db.add(LoginToken(token_hash="a", poll_secret_hash="b", expires_at=now - timedelta(days=2)))
    db.add(LoginToken(token_hash="c", poll_secret_hash="d", expires_at=now))
    db.add(Message(conversation_id=conv.id, direction="inbound", kind="text", status="processed",
                   created_at=now - timedelta(days=get_settings().message_retention_days + 1)))
    db.commit()
    tasks.purge_expired()
    db.expire_all()
    assert db.get(GeneratedAsset, expired.id).deleted_at is not None and not st.exists("reels/old.mp4")
    assert db.get(GeneratedAsset, keep.id).deleted_at is None
    assert db.get(ProjectAsset, old_src.id).status == "deleted" and not st.exists("src/old.mp4")
    assert db.query(LoginToken).count() == 1 and db.query(Message).count() == 0


@pytest.mark.parametrize("anonymous,kept", [(True, False), (False, True)])
def test_purge_expired_projects(db, world, anonymous, kept):
    user, _, p = world
    user.is_anonymous = anonymous
    pid = p.id
    p.expires_at = utcnow() - timedelta(minutes=1)
    get_storage().put_bytes("src/p.mp4", b"x")
    add_asset(db, p, storage_key="src/p.mp4")
    add_asset(db, p)  # no storage key
    tasks.purge_expired()
    db.expire_all()
    assert (db.get(VideoProject, pid) is not None) is kept
    assert not get_storage().exists("src/p.mp4")


def test_safe_delete_swallows_errors():
    class Boom:
        def delete(self, key):
            raise OSError("nope")

    tasks._safe_delete(Boom(), "k")
    tasks._safe_delete(Boom(), None)
