"""Durable task queue on Postgres.

Why not Redis/Celery for the MVP: tasks are inserted in the *same transaction* as the
domain change that caused them (e.g. job row + its generate task), so a task can never
be lost or exist without its job. Claiming uses SELECT … FOR UPDATE SKIP LOCKED, so
several workers can run safely. dedupe_key makes enqueueing idempotent.
"""
from __future__ import annotations

import logging
import socket
import os
from datetime import timedelta
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import TaskQueue, utcnow

log = logging.getLogger(__name__)
WORKER_ID = f"{socket.gethostname()}:{os.getpid()}"


class RetryableTaskError(Exception):
    """Raise from a handler to retry with exponential backoff."""


def enqueue(db: Session, task_type: str, payload: dict[str, Any], *, dedupe_key: str | None = None,
            max_attempts: int | None = None, delay_seconds: float = 0) -> TaskQueue:
    if dedupe_key:
        existing = db.scalar(select(TaskQueue).where(TaskQueue.dedupe_key == dedupe_key))
        if existing:
            return existing
    task = TaskQueue(
        task_type=task_type, payload=payload, dedupe_key=dedupe_key,
        max_attempts=max_attempts or get_settings().job_max_attempts,
        run_after=utcnow() + timedelta(seconds=delay_seconds),
    )
    try:
        with db.begin_nested():
            db.add(task)
            db.flush()
    except IntegrityError:
        existing = db.scalar(select(TaskQueue).where(TaskQueue.dedupe_key == dedupe_key))
        if existing is None:
            raise
        return existing
    return task


def claim(db: Session, task_types: list[str] | None = None) -> TaskQueue | None:
    q = (select(TaskQueue)
         .where(TaskQueue.status == "pending", TaskQueue.run_after <= utcnow())
         .order_by(TaskQueue.id).limit(1).with_for_update(skip_locked=True))
    if task_types:
        q = q.where(TaskQueue.task_type.in_(task_types))
    task = db.scalar(q)
    if task is None:
        db.rollback()
        return None
    task.status = "running"
    task.attempts += 1
    task.locked_by = WORKER_ID
    task.locked_at = utcnow()
    db.commit()
    return task


def complete(db: Session, task: TaskQueue) -> None:
    task.status = "done"
    task.finished_at = utcnow()
    task.locked_by = None
    db.commit()


def fail(db: Session, task: TaskQueue, error: str, retryable: bool = True) -> bool:
    """Record a failure. Returns True if the task is now dead (dead-letter)."""
    task.last_error = error[:1000]
    task.locked_by = None
    if retryable and task.attempts < task.max_attempts:
        backoff = get_settings().job_retry_base_seconds * (2 ** (task.attempts - 1))
        task.status = "pending"
        task.run_after = utcnow() + timedelta(seconds=backoff)
        db.commit()
        return False
    task.status = "dead"
    task.finished_at = utcnow()
    db.commit()
    return True


def recover_stale(db: Session, older_than_seconds: int = 900) -> int:
    """Return crashed workers' tasks to the queue."""
    cutoff = utcnow() - timedelta(seconds=older_than_seconds)
    res = db.execute(update(TaskQueue)
                     .where(TaskQueue.status == "running", TaskQueue.locked_at < cutoff)
                     .values(status="pending", locked_by=None))
    db.commit()
    return res.rowcount or 0
