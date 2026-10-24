"""Email delivery (verification links). console = log only (development)."""
from __future__ import annotations

import logging
import smtplib
from email.message import EmailMessage

from app.config import get_settings

log = logging.getLogger(__name__)
# Last messages sent by the console provider (dev only; shown by /api/dev/emails).
DEV_OUTBOX: list[dict] = []


def send_email(to: str, subject: str, body: str) -> None:
    s = get_settings()
    if s.email_provider == "smtp":
        msg = EmailMessage()
        msg["From"], msg["To"], msg["Subject"] = s.smtp_from, to, subject
        msg.set_content(body)
        with smtplib.SMTP(s.smtp_host or "localhost", s.smtp_port, timeout=20) as smtp:
            smtp.starttls()
            if s.smtp_username:
                smtp.login(s.smtp_username, s.smtp_password or "")
            smtp.send_message(msg)
        return
    DEV_OUTBOX.append({"to": to, "subject": subject, "body": body})
    del DEV_OUTBOX[:-20]
    log.info("console email queued: %s", subject)
