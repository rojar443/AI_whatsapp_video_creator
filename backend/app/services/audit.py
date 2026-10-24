from typing import Any

from sqlalchemy.orm import Session

from app.models import AuditLog


def audit(db: Session, action: str, *, actor_id: str | None = None, target_type: str | None = None,
          target_id: str | None = None, details: dict[str, Any] | None = None, ip: str | None = None) -> None:
    """Append-only audit trail. Never put secrets, passwords or message contents in details."""
    db.add(AuditLog(actor_user_id=actor_id, action=action, target_type=target_type,
                    target_id=target_id, details=details or {}, ip=ip))
