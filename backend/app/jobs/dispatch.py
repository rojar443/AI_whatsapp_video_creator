"""Task registry + enqueue-after-commit.

`enqueue(db, name, *args)` sends the task only after the surrounding DB transaction commits
(nothing is sent if it rolls back), so a worker never sees a job that doesn't exist yet.
Tasks are idempotent: each re-checks DB state before acting, and a periodic sweeper
re-sends work that got lost (broker restart, crash).

TASK_BACKEND=celery → Celery/Redis.  TASK_BACKEND=inline → an in-process FIFO, used by tests
(drain()) — the same retry/final-failure rules apply in both modes.
"""
from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, field

from sqlalchemy import event
from sqlalchemy.orm import Session

from app.config import get_settings

log = logging.getLogger(__name__)


class RetryableError(Exception):
    """Raise from a task to retry with exponential backoff."""


@dataclass
class TaskDef:
    fn: Callable
    queue: str = "default"
    max_retries: int = 0
    on_final_failure: Callable | None = None


REGISTRY: dict[str, TaskDef] = {}


def task(name: str, queue: str = "default", max_retries: int = 0, on_final_failure: Callable | None = None):
    def deco(fn):
        REGISTRY[name] = TaskDef(fn, queue, max_retries, on_final_failure)
        return fn
    return deco


def backoff(attempt: int) -> float:
    return get_settings().job_retry_base_seconds * (2 ** (attempt - 1))


# ---------------------------------------------------------------- enqueue
def enqueue(db: Session | None, name: str, *args, countdown: float | None = None) -> None:
    if name not in REGISTRY:
        _ensure_loaded()
    if db is None:
        _send(name, args, countdown)
        return
    pending = db.info.setdefault("pending_tasks", [])
    pending.append((name, args, countdown))
    if not db.info.get("dispatch_hooked"):
        db.info["dispatch_hooked"] = True
        event.listen(db, "after_commit", _after_commit)
        event.listen(db, "after_soft_rollback", _after_rollback)


def _after_commit(session: Session) -> None:
    pending = session.info.pop("pending_tasks", [])
    for name, args, countdown in pending:
        _send(name, args, countdown)


def _after_rollback(session: Session, previous_transaction) -> None:
    if previous_transaction.parent is None:  # outermost rollback only
        session.info.pop("pending_tasks", None)


def _send(name: str, args: tuple, countdown: float | None, attempt: int = 1) -> None:
    if get_settings().task_backend == "inline":
        INLINE.add(name, args, countdown, attempt)
        return
    from app.jobs.celery_app import celery_app

    queue = REGISTRY[name].queue
    celery_app.send_task(f"reelchat.run_{queue}", args=[name, list(args), attempt], queue=queue,
                         countdown=countdown)


def _ensure_loaded() -> None:
    import app.jobs.tasks  # noqa: F401  (registers tasks)


# ---------------------------------------------------------------- execution (shared)
def execute(name: str, args: list, attempt: int, retry: Callable[[float], None] | None = None) -> None:
    _ensure_loaded()
    td = REGISTRY[name]
    try:
        td.fn(*args)
    except RetryableError as e:
        if attempt <= td.max_retries:
            delay = backoff(attempt)
            log.warning("task %s attempt %s failed (%s); retrying in %ss", name, attempt, e, delay)
            if retry is not None:
                retry(delay)
            else:
                _send(name, tuple(args), delay, attempt + 1)
            return
        log.error("task %s exhausted retries: %s", name, e)
        if td.on_final_failure:
            td.on_final_failure(*args, error=str(e))
    except Exception as e:  # noqa: BLE001
        log.exception("task %s crashed", name)
        if td.on_final_failure:
            td.on_final_failure(*args, error=f"{type(e).__name__}: {e}")
        else:
            raise


# ---------------------------------------------------------------- inline backend
@dataclass
class _Inline:
    ready: list = field(default_factory=list)
    delayed: list = field(default_factory=list)

    def add(self, name, args, countdown, attempt):
        (self.delayed if countdown else self.ready).append((name, list(args), attempt))

    def clear(self):
        self.ready.clear()
        self.delayed.clear()

    def drain(self, include_delayed: bool = False, limit: int = 500) -> int:
        n = 0
        while n < limit:
            if not self.ready and include_delayed and self.delayed:
                self.ready, self.delayed = self.delayed, []
            if not self.ready:
                break
            name, args, attempt = self.ready.pop(0)
            execute(name, args, attempt)
            n += 1
        return n

    def run_delayed(self, name: str | None = None) -> int:
        """Run delayed tasks (optionally only one task type) once, then drain."""
        keep, run = [], []
        for t in self.delayed:
            (run if name is None or t[0] == name else keep).append(t)
        self.delayed = keep
        self.ready.extend(run)
        return self.drain()


INLINE = _Inline()
