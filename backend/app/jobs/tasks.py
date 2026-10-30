"""Background tasks. Every task re-reads DB state first, so duplicates are harmless."""
from __future__ import annotations

import logging
import shutil
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

from sqlalchemy import delete, func, select

from app import providers
from app.config import get_settings
from app.conversation import replies
from app.db import get_sessionmaker
from app.jobs.dispatch import RetryableError, enqueue, task
from app.media.intake import MediaRejected, inspect
from app.messaging.base import SendError
from app.models import (
    Conversation,
    GeneratedAsset,
    GenerationJob,
    JobStatus,
    LoginToken,
    Message,
    ProjectAsset,
    ProjectStatus,
    User,
    VideoPreferences,
    VideoProject,
    aware,
    utcnow,
)
from app.pipeline.plan_text_labels import asset_labels
from app.pipeline.registry import StepError
from app.services import credits
from app.services.outbox import Outbox, deliver_batch
from app.storage import get_storage

log = logging.getLogger(__name__)
S = ProjectStatus


def session():
    return get_sessionmaker()()


def _outbox(db, project: VideoProject) -> Outbox | None:
    conv = db.get(Conversation, project.conversation_id) if project.conversation_id else None
    if conv is None:
        return None
    return Outbox(db, conv, project.id)


# ---------------------------------------------------------------- inbound
@task("process_inbound")
def process_inbound(message_id: str) -> None:
    from app.conversation.engine import ConversationEngine

    with session() as db:
        ConversationEngine(db).handle_inbound(message_id)


@task("download_asset", max_retries=3)
def download_asset(asset_id: str) -> None:
    s = get_settings()
    with session() as db:
        a = db.get(ProjectAsset, asset_id)
        if a is None or a.status != "pending":
            return
        project = db.get(VideoProject, a.project_id)
        limit = s.max_video_bytes if a.kind == "video" else s.max_image_bytes
        tmp_root = Path(s.work_dir) / "intake"
        tmp_root.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=tmp_root) as tmp:
            dest = str(Path(tmp) / "media")
            try:
                mime = providers.get_messaging().download_media(a.media_id or "", dest, limit) or a.mime_type
            except SendError as e:
                if e.retryable:
                    raise RetryableError(str(e)) from e
                _reject(db, a, project, "file is too large" if e.code == "too_large" else None, failed=True)
                return
            size = Path(dest).stat().st_size
            try:
                info = inspect(dest, mime or a.mime_type, size)
            except MediaRejected as e:
                _reject(db, a, project, e.reason)
                return
            key = f"users/{a.user_id}/projects/{a.project_id}/src/{a.id}.{info.ext}"  # never the user's file name
            get_storage().put_file(key, dest, mime or "application/octet-stream")
        a.status, a.storage_key, a.mime_type, a.size_bytes = "ready", key, mime, size
        a.kind, a.duration_seconds, a.width, a.height = info.kind, info.duration, info.width, info.height
        a.codec, a.has_audio = info.codec, info.has_audio
        db.commit()


def _reject(db, a: ProjectAsset, project: VideoProject, reason: str | None, failed: bool = False) -> None:
    a.status = "failed" if failed and not reason else "rejected"
    a.reject_reason = (reason or "download failed")[:200]
    label = asset_labels(db, project.id).get(a.id, "a file")
    out = _outbox(db, project)
    if out:
        out.text(replies.render("media_rejected" if reason else "media_failed", {"what": label, "reason": reason}))
        out.flush()
    db.commit()


def download_failed(asset_id: str, error: str = "") -> None:
    with session() as db:
        a = db.get(ProjectAsset, asset_id)
        if a and a.status == "pending":
            _reject(db, a, db.get(VideoProject, a.project_id), None, failed=True)



@task("batch_ack")
def batch_ack(project_id: str, expected: int) -> None:
    """Debounced "I received N clips" — only for the latest upload in a burst."""
    from app.conversation.engine import summary_of

    with session() as db:
        p = db.get(VideoProject, project_id)
        if p is None or p.status != S.collecting_media.value:
            return
        n = db.scalar(select(func.count(ProjectAsset.id)).where(
            ProjectAsset.project_id == project_id, ProjectAsset.status.in_(["pending", "ready"])))
        if n != expected:
            return
        row = db.scalar(select(VideoPreferences).where(VideoPreferences.project_id == p.id,
                                                       VideoPreferences.revision == p.revision))
        has_prefs = bool(row and (row.genre or row.vibe or row.instructions))
        out = _outbox(db, p)
        if out:
            out.text(replies.render("batch_ack_prefs" if has_prefs else "batch_ack",
                                    {"summary": summary_of(db, p.id)}))
            out.flush()
        db.commit()


@task("batch_timeout")
def batch_timeout(project_id: str, last_media_iso: str) -> None:
    from app.conversation.engine import ConversationEngine

    with session() as db:
        p = db.get(VideoProject, project_id)
        if p is None or p.status != S.collecting_media.value or p.last_media_at is None:
            return
        if aware(p.last_media_at).isoformat() != aware(datetime.fromisoformat(last_media_iso)).isoformat():
            return  # more media arrived since; a later timeout will handle it
        out = _outbox(db, p)
        if out is None:
            return
        ConversationEngine(db).close_batch(p, out)
        out.flush()
        db.commit()


@task("plan_project", queue="media", max_retries=2)
def plan_project(project_id: str, revision: int) -> None:
    from app.conversation.engine import ConversationEngine
    from app.pipeline.run import build_plan, work_dir

    s = get_settings()
    with session() as db, credits.persist_usage(db):
        p = db.get(VideoProject, project_id)
        if p is None or p.revision != revision or p.status != S.awaiting_preferences.value or p.stage != "planning":
            return
        pending = db.scalar(select(ProjectAsset.id).where(ProjectAsset.project_id == p.id,
                                                          ProjectAsset.status == "pending").limit(1))
        if pending and utcnow() - aware(p.last_media_at or p.created_at) < timedelta(minutes=5):
            enqueue(None, "plan_project", project_id, revision, countdown=3)  # wait for downloads
            return
        row = db.scalar(select(VideoPreferences).where(VideoPreferences.project_id == p.id,
                                                       VideoPreferences.revision == revision))
        try:
            plan = build_plan(db, p, row)
        except StepError as e:
            if e.retryable:
                raise RetryableError(str(e)) from e
            return _plan_failed(db, p, str(e))
        except ValueError as e:
            return _plan_failed(db, p, str(e))
        finally:
            shutil.rmtree(work_dir(f"plan-{p.id}-r{revision}") / "frames", ignore_errors=True)
        row.plan = plan
        row.playbook_version = plan.get("playbook")
        out = _outbox(db, p)
        engine = ConversationEngine(db)
        if out:
            engine.present_plan(p, out)
            out.flush()
        else:
            p.status, p.stage = S.awaiting_confirmation.value, None
        db.commit()
        log.info("planned project %s r%s with %s moments (%s)", p.id, revision, len(plan["items"]),
                 s.pipeline_moment_scoring)


def _plan_failed(db, p: VideoProject, reason: str) -> None:
    p.stage = "plan_failed"
    p.last_error = reason[:500]
    out = _outbox(db, p)
    if out:
        out.text(replies.render("plan_failed", {"reason": reason[:120]}))
        out.flush()
    db.commit()


def plan_final_failure(project_id: str, revision: int, error: str = "") -> None:
    with session() as db:
        p = db.get(VideoProject, project_id)
        if p is not None and p.stage == "planning":
            _plan_failed(db, p, "a processing service is unavailable")


# ---------------------------------------------------------------- render
@task("render_job", queue="media", max_retries=2)
def render_job(job_id: str) -> None:
    from app.media.ffmpeg import FFmpegError
    from app.pipeline.run import RenderFailure, render_job as run_render

    s = get_settings()
    with session() as db, credits.persist_usage(db):
        job = db.scalar(select(GenerationJob).where(GenerationJob.id == job_id).with_for_update())
        if job is None or job.status in (JobStatus.completed.value, JobStatus.cancelled.value, JobStatus.failed.value):
            db.rollback()
            return
        project = db.get(VideoProject, job.project_id)
        first = job.status == JobStatus.queued.value
        job.status, job.attempts = JobStatus.processing.value, job.attempts + 1
        job.started_at = job.started_at or utcnow()
        project.status = S.processing.value
        if first:
            out = _outbox(db, project)
            if out:
                out.text(replies.render("processing"))
                out.flush()
        db.commit()

        def on_stage(stage: str) -> None:
            job.stage = stage
            db.commit()

        try:
            result = run_render(db, job, on_stage)
        except StepError as e:
            db.rollback()
            if e.retryable:
                raise RetryableError(f"{e.code}: {e}") from e
            return fail_job(job_id, e.code, str(e))
        except RenderFailure as e:
            db.rollback()
            return fail_job(job_id, e.code, str(e))
        except FFmpegError as e:
            db.rollback()
            log.error("ffmpeg failed for job %s: %s", job_id, e)
            return fail_job(job_id, "render_failed", "video processing failed")
        job.status, job.stage, job.finished_at = JobStatus.completed.value, "done", utcnow()
        project.status, project.last_error = S.completed.value, None
        project.title = (result["title"] or project.title)[:200]
        asset = result["asset"]
        credits.record_cost(db, user_id=job.user_id, job_id=job.id, kind="render", provider="ffmpeg",
                            quantity=asset.duration_seconds or 0, unit="seconds", cost_usd=0)
        out = _outbox(db, project)
        if out:
            if result["demo"]:
                out.text(replies.render("demo_notice", {"what": ", ".join(result["demo"])}))
            hours = s.download_url_ttl_seconds // 3600
            out.video(asset, replies.render("delivered"),
                      replies.render("delivered_link", {"title": result["title"], "hours": hours}),
                      "Want any changes? Just tell me.")
            owner = db.get(User, job.user_id)
            if owner and owner.is_anonymous:
                from urllib.parse import quote

                conv = db.get(Conversation, project.conversation_id)
                out.text(replies.render("anon_register_nudge", {
                    "web_url": s.web_base_url, "phone": quote("+" + conv.whatsapp_account.wa_id)}))
            out.flush()
        db.commit()


def fail_job(job_id: str, code: str, message: str) -> None:
    with session() as db:
        job = db.scalar(select(GenerationJob).where(GenerationJob.id == job_id).with_for_update())
        if job is None or job.status in (JobStatus.completed.value, JobStatus.cancelled.value, JobStatus.failed.value):
            db.rollback()
            return
        project = db.get(VideoProject, job.project_id)
        job.status, job.error_code, job.error_message = JobStatus.failed.value, code[:60], message[:500]
        job.finished_at = utcnow()
        refunded = credits.refund(db, job)
        project.status, project.last_error = S.failed.value, message[:500]
        reason = {"agent_unreachable": "a processing service was unreachable", "narration_failed": "the voiceover failed",
                  "invalid_output": "the result didn't pass quality checks"}.get(code, "a processing error")
        out = _outbox(db, project)
        if out:
            out.buttons(replies.render("failed", {"reason": reason, "credits": refunded}), [(f"retry:{job.id}", "🔁 Retry")])
            out.flush()
        db.commit()


def render_final_failure(job_id: str, error: str = "") -> None:
    fail_job(job_id, "agent_unreachable" if "agent" in error else "render_failed", error or "retries exhausted")


# ---------------------------------------------------------------- outbound
@task("deliver", max_retries=4)
def deliver(message_ids: list[str]) -> None:
    with session() as db:
        deliver_batch(db, message_ids, providers.get_messaging())


def deliver_final_failure(message_ids: list[str], error: str = "") -> None:
    with session() as db:
        for mid in message_ids:
            m = db.get(Message, mid)
            if m and m.status == "pending":
                m.status, m.error = "failed", (error or "delivery failed")[:500]
        db.commit()


# ---------------------------------------------------------------- maintenance
@task("sweep")
def sweep() -> None:
    """Re-send work that may have been lost (broker restart, crashed worker)."""
    s = get_settings()
    now = utcnow()
    with session() as db:
        for m in db.scalars(select(Message).where(Message.direction == "inbound", Message.status == "received",
                                                  Message.created_at < now - timedelta(seconds=60))).all():
            enqueue(None, "process_inbound", m.id)
        stale_out = db.scalars(select(Message.id).where(Message.direction == "outbound", Message.status == "pending",
                                                        Message.created_at < now - timedelta(minutes=2))).all()
        if stale_out:
            enqueue(None, "deliver", list(stale_out))
        for a in db.scalars(select(ProjectAsset).where(ProjectAsset.status == "pending",
                                                       ProjectAsset.created_at < now - timedelta(minutes=3))).all():
            enqueue(None, "download_asset", a.id)
        for j in db.scalars(select(GenerationJob).where(GenerationJob.status == JobStatus.queued.value,
                                                        GenerationJob.created_at < now - timedelta(minutes=5))).all():
            enqueue(None, "render_job", j.id)
        for j in db.scalars(select(GenerationJob).where(GenerationJob.status == JobStatus.processing.value,
                                                        GenerationJob.started_at < now - timedelta(minutes=45))).all():
            fail_job(j.id, "timeout", "processing took too long")
        for p in db.scalars(select(VideoProject).where(VideoProject.status == S.collecting_media.value,
                                                       VideoProject.last_media_at < now - timedelta(
                                                           seconds=s.batch_timeout_seconds + 60))).all():
            enqueue(None, "batch_timeout", p.id, aware(p.last_media_at).isoformat())
        for p in db.scalars(select(VideoProject).where(VideoProject.stage == "planning",
                                                       VideoProject.updated_at < now - timedelta(minutes=10))).all():
            enqueue(None, "plan_project", p.id, p.revision)


@task("purge_expired")
def purge_expired() -> None:
    """Retention: expired reels, old source media, old messages."""
    s = get_settings()
    now = utcnow()
    storage = get_storage()
    with session() as db:
        for a in db.scalars(select(GeneratedAsset).where(GeneratedAsset.expires_at < now,
                                                         GeneratedAsset.deleted_at.is_(None))).all():
            _safe_delete(storage, a.storage_key)
            a.deleted_at = now
        src_cutoff = now - timedelta(days=s.source_media_retention_days)
        for a in db.scalars(select(ProjectAsset).where(ProjectAsset.status == "ready",
                                                       ProjectAsset.created_at < src_cutoff)).all():
            _safe_delete(storage, a.storage_key)
            a.status = "deleted"
        for p in db.scalars(select(VideoProject).where(VideoProject.expires_at < now)).all():
            for a in db.scalars(select(ProjectAsset).where(ProjectAsset.project_id == p.id)).all():
                if a.storage_key:
                    _safe_delete(storage, a.storage_key)
            owner = db.get(User, p.user_id)
            if owner and owner.is_anonymous:
                db.delete(p)  # anonymous drafts are removed entirely
        db.execute(delete(LoginToken).where(LoginToken.expires_at < now - timedelta(days=1)))
        db.execute(delete(Message).where(Message.created_at < now - timedelta(days=s.message_retention_days)))
        db.commit()


def _safe_delete(storage, key: str | None) -> None:
    if not key:
        return
    try:
        storage.delete(key)
    except Exception:  # noqa: BLE001
        log.warning("could not delete %s", key)


# final-failure hooks (set after definitions to avoid forward references)
from app.jobs.dispatch import REGISTRY  # noqa: E402

REGISTRY["download_asset"].on_final_failure = download_failed
REGISTRY["plan_project"].on_final_failure = plan_final_failure
REGISTRY["render_job"].on_final_failure = render_final_failure
REGISTRY["deliver"].on_final_failure = deliver_final_failure
