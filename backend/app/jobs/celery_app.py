"""Celery wiring. Run: celery -A app.jobs.celery_app worker -B -Q default,media"""
from __future__ import annotations

from celery import Celery
from celery.signals import worker_process_init

from app.config import get_settings

s = get_settings()
celery_app = Celery("reelchat", broker=s.redis_url, backend=None)
celery_app.conf.update(
    task_acks_late=True,  # a crashed worker's task is redelivered
    task_reject_on_worker_lost=True,
    worker_prefetch_multiplier=1,
    task_default_queue="default",
    broker_connection_retry_on_startup=True,
    task_time_limit=1800,
    task_soft_time_limit=1700,
    beat_schedule={
        "sweep": {"task": "reelchat.run_default", "schedule": 60.0, "args": ["sweep", [], 1]},
        "purge": {"task": "reelchat.run_default", "schedule": 3600.0, "args": ["purge_expired", [], 1]},
    },
)


@worker_process_init.connect
def _init(**_):
    from app.logging_setup import setup_logging

    setup_logging()


def _run(self, name: str, args: list, attempt: int = 1):
    from app.jobs.dispatch import _send, execute

    def retry(delay: float) -> None:
        _send(name, tuple(args), delay, attempt + 1)

    execute(name, args, attempt, retry)


@celery_app.task(name="reelchat.run_default", bind=True)
def run_default(self, name: str, args: list, attempt: int = 1):
    _run(self, name, args, attempt)


@celery_app.task(name="reelchat.run_media", bind=True)
def run_media(self, name: str, args: list, attempt: int = 1):
    _run(self, name, args, attempt)
