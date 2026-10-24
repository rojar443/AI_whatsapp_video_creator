"""Background task handlers."""
from __future__ import annotations

import hashlib
import json
import logging
import shutil
from datetime import timedelta
from pathlib import Path

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app import providers
from app.config import get_settings
from app.conversation import replies
from app.jobs import queue
from app.models import (
    Conversation,
    GeneratedAsset,
    GenerationJob,
    JobStatus,
    Message,
    ProjectStatus,
    TaskQueue,
    VideoPreferences,
    VideoProject,
    utcnow,
)
from app.services import credits
from app.services.outbox import Outbox, deliver_batch
from app.storage import get_storage
from app.video.base import GenerationError
from app.video.ffmpeg import FFmpegError

log = logging.getLogger(__name__)


# ---------------------------------------------------------------- inbound
def process_inbound(db: Session, payload: dict, task: TaskQueue) -> None:
    from app.conversation.engine import ConversationEngine

    ConversationEngine(db).handle_inbound(payload["message_id"])


# ---------------------------------------------------------------- outbound
def deliver(db: Session, payload: dict, task: TaskQueue) -> None:
    deliver_batch(db, payload["message_ids"], providers.get_messaging())


def deliver_dead(db: Session, payload: dict, task: TaskQueue) -> None:
    for mid in payload.get("message_ids", []):
        m = db.get(Message, mid)
        if m and m.status == "pending":
            m.status = "failed"
    db.commit()


# ---------------------------------------------------------------- generation
def _job_context(db: Session, job: GenerationJob) -> tuple[VideoProject, Conversation | None]:
    project = db.get(VideoProject, job.project_id)
    conv = db.get(Conversation, project.conversation_id) if project.conversation_id else None
    return project, conv


def _notify(db: Session, conv: Conversation | None, project_id: str, build) -> None:
    if conv is None or conv.whatsapp_account is None or conv.whatsapp_account.user_id is None:
        return
    out = Outbox(db, conv, project_id)
    build(out)
    out.flush()


def generate_video(db: Session, payload: dict, task: TaskQueue) -> None:
    s = get_settings()
    job = db.scalar(select(GenerationJob).where(GenerationJob.id == payload["job_id"]).with_for_update())
    if job is None or job.status in (JobStatus.completed.value, JobStatus.cancelled.value, JobStatus.failed.value):
        db.rollback()
        return  # idempotent: duplicate or stale task
    project, conv = _job_context(db, job)
    first_attempt = job.status == JobStatus.queued.value
    job.status = JobStatus.processing.value
    job.attempts += 1
    job.started_at = job.started_at or utcnow()
    project.status = ProjectStatus.processing.value
    if first_attempt:
        _notify(db, conv, project.id, lambda o: o.text(replies.render("processing")))
    db.commit()

    prefs_row = db.scalar(select(VideoPreferences).where(
        VideoPreferences.project_id == project.id, VideoPreferences.revision == job.revision))
    prefs = {c: getattr(prefs_row, c) for c in (
        "topic", "vibe", "format", "aspect_ratio", "duration_seconds", "visual_style", "quality", "language",
        "voice", "captions_enabled", "caption_language", "music", "pace", "brand_colors", "call_to_action",
        "script_mode", "user_script")}
    provider = providers.get_video_provider(db)
    workdir = Path(s.work_dir) / f"{job.id}-{job.run_number}-{job.attempts}"
    workdir.mkdir(parents=True, exist_ok=True)

    def on_stage(stage: str) -> None:
        job.stage = stage
        db.commit()

    try:
        result = provider.render(prefs, str(workdir), on_stage)
    except GenerationError as e:
        db.rollback()
        if e.retryable:
            raise queue.RetryableTaskError(f"{e.code}: {e}") from e
        fail_job(db, job.id, e.code, str(e))
        return
    except FFmpegError as e:
        db.rollback()
        log.error("ffmpeg failed for job %s: %s", job.id, e)
        fail_job(db, job.id, "render_failed", "video rendering failed")
        return
    finally:
        pass

    storage = get_storage()
    base = f"users/{job.user_id}/projects/{project.id}/r{job.revision}-{job.id[:8]}-{job.run_number}"
    video_key = base + ".mp4"
    size = storage.put_file(video_key, result.path, "video/mp4")
    sha = hashlib.sha256(Path(result.path).read_bytes()).hexdigest()
    expires = utcnow() + timedelta(days=s.asset_retention_days)
    asset = GeneratedAsset(
        user_id=job.user_id, project_id=project.id, job_id=job.id, kind="video", storage_key=video_key,
        mime_type="video/mp4", size_bytes=size, duration_seconds=result.seconds, width=result.width,
        height=result.height, checksum_sha256=sha, is_mock=provider.is_mock, expires_at=expires,
        extra={"title": result.script.title, "hashtags": result.script.hashtags, "provider": provider.name,
               "tts": provider.tts.name},
    )
    db.add(asset)
    script_key = base + ".script.json"
    script_bytes = json.dumps(result.script.model_dump(), ensure_ascii=False, indent=2).encode()
    storage.put_bytes(script_key, script_bytes, "application/json")
    db.add(GeneratedAsset(user_id=job.user_id, project_id=project.id, job_id=job.id, kind="script",
                          storage_key=script_key, mime_type="application/json", size_bytes=len(script_bytes),
                          is_mock=provider.is_mock, expires_at=expires))
    if result.srt_path:
        srt_key = base + ".srt"
        n = storage.put_file(srt_key, result.srt_path, "application/x-subrip")
        db.add(GeneratedAsset(user_id=job.user_id, project_id=project.id, job_id=job.id, kind="subtitles",
                              storage_key=srt_key, mime_type="application/x-subrip", size_bytes=n,
                              is_mock=provider.is_mock, expires_at=expires))
    job.status = JobStatus.completed.value
    job.stage = "done"
    job.finished_at = utcnow()
    job.estimated_cost_usd = round(provider.cost_per_second_usd * result.seconds, 4)
    project.status = ProjectStatus.completed.value
    project.last_error = None
    credits.record_cost(db, user_id=job.user_id, job_id=job.id, kind="video", provider=provider.name,
                        quantity=result.seconds, unit="seconds", cost_usd=float(job.estimated_cost_usd))
    db.flush()

    title = result.script.title or project.title
    hours = s.download_url_ttl_seconds // 3600

    def build(out: Outbox) -> None:
        if provider.is_mock:
            out.text(replies.render("mock_notice"))
        out.video(asset, replies.render("delivered_video", {"title": title}),
                  replies.render("delivered_link", {"title": title, "hours": hours}),
                  replies.render("change_hint"))

    _notify(db, conv, project.id, build)
    db.commit()
    shutil.rmtree(workdir, ignore_errors=True)


def fail_job(db: Session, job_id: str, code: str, message: str) -> None:
    job = db.scalar(select(GenerationJob).where(GenerationJob.id == job_id).with_for_update())
    if job is None or job.status in (JobStatus.completed.value, JobStatus.cancelled.value, JobStatus.failed.value):
        db.rollback()
        return
    project, conv = _job_context(db, job)
    job.status = JobStatus.failed.value
    job.error_code = code[:60]
    job.error_message = message[:500]
    job.finished_at = utcnow()
    refunded = credits.refund(db, job)
    project.status = ProjectStatus.failed.value
    project.last_error = message[:500]
    reason = {"provider_timeout": "the video service timed out", "provider_unavailable": "the video service is busy",
              "invalid_output": "the result didn't pass quality checks"}.get(code, "an internal error")

    def build(out: Outbox) -> None:
        out.buttons(replies.render("failed", {"reason": reason, "credits": refunded}),
                    [(f"retry:{job.id}", "🔁 Retry")])

    _notify(db, conv, project.id, build)
    db.commit()


def generate_video_dead(db: Session, payload: dict, task: TaskQueue) -> None:
    fail_job(db, payload["job_id"], "provider_unavailable", task.last_error or "retries exhausted")


# ---------------------------------------------------------------- maintenance
def purge_expired(db: Session, payload: dict, task: TaskQueue) -> None:
    """Retention policy: delete old messages, expired assets and finished queue rows."""
    s = get_settings()
    now = utcnow()
    storage = get_storage()
    for asset in db.scalars(select(GeneratedAsset).where(
            GeneratedAsset.expires_at < now, GeneratedAsset.deleted_at.is_(None))).all():
        try:
            storage.delete(asset.storage_key)
        except Exception:  # noqa: BLE001
            log.warning("could not delete %s", asset.storage_key)
        asset.deleted_at = now
    db.execute(delete(Message).where(Message.created_at < now - timedelta(days=s.message_retention_days)))
    db.execute(delete(TaskQueue).where(TaskQueue.status == "done", TaskQueue.finished_at < now - timedelta(days=7)))
    db.commit()


HANDLERS = {
    "process_inbound": (process_inbound, None),
    "deliver": (deliver, deliver_dead),
    "generate_video": (generate_video, generate_video_dead),
    "purge_expired": (purge_expired, None),
}


def run_task(db: Session, task: TaskQueue) -> None:
    handler, on_dead = HANDLERS.get(task.task_type, (None, None))
    if handler is None:
        queue.fail(db, task, f"unknown task type {task.task_type}", retryable=False)
        return
    try:
        handler(db, task.payload or {}, task)
    except queue.RetryableTaskError as e:
        db.rollback()
        task = db.get(TaskQueue, task.id)
        if queue.fail(db, task, str(e), retryable=True) and on_dead:
            on_dead(db, task.payload or {}, task)
        return
    except Exception as e:  # noqa: BLE001
        db.rollback()
        log.exception("task %s (%s) crashed", task.id, task.task_type)
        task = db.get(TaskQueue, task.id)
        if queue.fail(db, task, f"{type(e).__name__}: {e}", retryable=True) and on_dead:
            on_dead(db, task.payload or {}, task)
        return
    task = db.get(TaskQueue, task.id)
    queue.complete(db, task)


def run_pending(db: Session, max_tasks: int = 100, include_delayed: bool = False) -> int:
    """Drain the queue synchronously (tests and the CLI). Returns tasks executed."""
    n = 0
    while n < max_tasks:
        if include_delayed:
            db.execute(TaskQueue.__table__.update().where(TaskQueue.status == "pending")
                       .values(run_after=utcnow() - timedelta(seconds=1)))
            db.commit()
        task = queue.claim(db)
        if task is None:
            break
        run_task(db, task)
        n += 1
    return n
