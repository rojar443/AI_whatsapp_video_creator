"""Worker process: `python -m app.jobs.worker`. Run several for more throughput."""
from __future__ import annotations

import logging
import signal
import time
from datetime import UTC, datetime

from app.config import get_settings
from app.db import get_sessionmaker
from app.jobs import queue
from app.jobs.tasks import run_task
from app.logging_setup import setup_logging

log = logging.getLogger("worker")
_stop = False


def _handle_sigterm(*_):
    global _stop
    _stop = True


def main() -> None:
    setup_logging()
    s = get_settings()
    signal.signal(signal.SIGTERM, _handle_sigterm)
    signal.signal(signal.SIGINT, _handle_sigterm)
    Session = get_sessionmaker()
    log.info("worker %s started", queue.WORKER_ID)
    last_maintenance = 0.0
    while not _stop:
        with Session() as db:
            if time.time() - last_maintenance > 300:
                queue.recover_stale(db)
                hour = datetime.now(UTC).strftime("%Y%m%d%H")
                queue.enqueue(db, "purge_expired", {}, dedupe_key=f"purge:{hour}")
                db.commit()
                last_maintenance = time.time()
            task = queue.claim(db)
            if task is None:
                time.sleep(s.worker_poll_seconds)
                continue
            log.info("running task %s %s (attempt %s)", task.id, task.task_type, task.attempts)
            run_task(db, task)
    log.info("worker stopped")


if __name__ == "__main__":
    main()
