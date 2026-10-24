import json
import logging
import re
import sys

from app.config import get_settings

_PHONE = re.compile(r"\+?\d{10,15}")
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+")


class RedactingFormatter(logging.Formatter):
    """JSON logs with phone numbers and emails masked. Message bodies are never logged."""

    def format(self, record: logging.LogRecord) -> str:
        msg = record.getMessage()
        msg = _PHONE.sub(lambda m: m.group(0)[:3] + "•••" + m.group(0)[-2:], msg)
        msg = _EMAIL.sub("•••@•••", msg)
        out = {"level": record.levelname, "logger": record.name, "msg": msg}
        if record.exc_info:
            out["exc"] = self.formatException(record.exc_info)[-2000:]
        return json.dumps(out)


def setup_logging() -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(RedactingFormatter())
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(logging.DEBUG if get_settings().app_env == "development" else logging.INFO)
    for noisy in ("httpx", "httpcore", "sqlalchemy.engine", "uvicorn.access", "botocore", "urllib3"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
