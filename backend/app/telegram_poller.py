"""Telegram long-polling worker: `python -m app.telegram_poller`.

Fetches updates from the Bot API and ingests them exactly like WhatsApp webhook messages
(stored once by unique message id, then processed by the worker). Needs no public URL.
"""
from __future__ import annotations

import logging
import signal
import time

from app import providers
from app.api.webhooks import ingest_message
from app.config import get_settings
from app.db import get_sessionmaker
from app.logging_setup import setup_logging
from app.messaging.base import SendError
from app.messaging.telegram import parse_update

log = logging.getLogger("telegram")
_stop = False


def handle_update(db, tg, update: dict) -> bool:
    """Ingest one update. Returns True if a message was stored."""
    m = parse_update(update)
    cb = update.get("callback_query")
    if isinstance(cb, dict) and tg is not None:
        tg.answer_callback(str(cb.get("id")))
    if m is None:
        return False
    stored = ingest_message(db, m) is not None
    db.commit()  # tasks are sent after this commit
    return stored


def main() -> None:
    global _stop
    setup_logging()
    tg = providers.get_telegram()
    if tg is None:
        raise SystemExit("TELEGRAM_BOT_TOKEN is not set")
    signal.signal(signal.SIGTERM, lambda *_: globals().update(_stop=True))
    signal.signal(signal.SIGINT, lambda *_: globals().update(_stop=True))
    s = get_settings()
    h = tg.health()
    log.info("telegram poller started: %s", h.get("bot") or h)
    Session = get_sessionmaker()
    offset: int | None = None
    backoff = 1.0
    while not _stop:
        try:
            updates = tg.get_updates(offset, s.telegram_poll_timeout)
            backoff = 1.0
        except SendError as e:
            log.warning("getUpdates failed (%s); retrying in %ss", e, backoff)
            time.sleep(backoff)
            backoff = min(30.0, backoff * 2)
            continue
        for u in updates:
            try:
                with Session() as db:
                    handle_update(db, tg, u)
            except Exception:  # noqa: BLE001 - one bad update must not stop the loop
                log.exception("failed to ingest telegram update %s", u.get("update_id"))
            offset = int(u["update_id"]) + 1
    log.info("telegram poller stopped")


if __name__ == "__main__":
    main()
