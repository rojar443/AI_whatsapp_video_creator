"""Credit ledger. Balance changes and their ledger rows commit together; idempotency keys
make reserve/refund safe to call twice (e.g. on a retried task)."""
from __future__ import annotations

from contextlib import contextmanager

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.models import GenerationJob, Message, UsageRecord, User, VideoProject


def _exists(db: Session, key: str) -> bool:
    return db.scalar(select(UsageRecord.id).where(UsageRecord.idempotency_key == key)) is not None


def reserve(db: Session, job: GenerationJob, amount: int) -> bool:
    key = f"reserve:{job.id}:{job.run_number}"
    if _exists(db, key):
        return True
    res = db.execute(
        update(User).where(User.id == job.user_id, User.credits_balance >= amount)
        .values(credits_balance=User.credits_balance - amount)
    )
    if (res.rowcount or 0) != 1:
        return False
    db.add(UsageRecord(user_id=job.user_id, job_id=job.id, kind="credit_reserve", credits_delta=-amount,
                       idempotency_key=key, quantity=amount, unit="credits"))
    job.credits_charged = amount
    db.flush()
    return True


def refund(db: Session, job: GenerationJob) -> int:
    amount = job.credits_charged or 0
    key = f"refund:{job.id}:{job.run_number}"
    if amount <= 0 or _exists(db, key):
        return 0
    db.execute(update(User).where(User.id == job.user_id)
               .values(credits_balance=User.credits_balance + amount))
    db.add(UsageRecord(user_id=job.user_id, job_id=job.id, kind="credit_refund", credits_delta=amount,
                       idempotency_key=key, quantity=amount, unit="credits"))
    job.credits_charged = 0
    db.flush()
    return amount


def grant(db: Session, user: User, amount: int, kind: str = "credit_grant", key: str | None = None) -> None:
    if key and _exists(db, key):
        return
    db.execute(update(User).where(User.id == user.id)
               .values(credits_balance=User.credits_balance + amount))
    db.add(UsageRecord(user_id=user.id, kind=kind, credits_delta=amount, idempotency_key=key,
                       quantity=amount, unit="credits"))
    db.flush()
    db.refresh(user)


def record_cost(db: Session, *, user_id: str | None, kind: str, provider: str | None, quantity: float,
                unit: str, cost_usd: float, job_id: str | None = None) -> None:
    db.add(UsageRecord(user_id=user_id, job_id=job_id, kind=kind, provider=provider, quantity=quantity,
                       unit=unit, estimated_cost_usd=round(cost_usd, 4)))


@contextmanager
def persist_usage(db: Session):
    """Provider usage survives business rollbacks, including failed pipeline attempts.

    Each actual call produces one row. Retrying work incurs additional usage.
    """
    db.info["capture_provider_usage"] = True
    try:
        yield
    finally:
        pending = db.info.pop("provider_usage", [])
        db.info.pop("capture_provider_usage", None)
        if pending:
            db.rollback()
            # A failed conversation savepoint can roll back a newly created project.
            # Keep its charged usage as unattributed rather than losing the ledger row.
            for values in pending:
                if values["project_id"] and db.get(VideoProject, values["project_id"]) is None:
                    values.update(project_id=None, revision=None)
                if values["user_id"] and db.get(User, values["user_id"]) is None:
                    values["user_id"] = None
                if values["message_id"] and db.get(Message, values["message_id"]) is None:
                    values["message_id"] = None
            db.add_all(UsageRecord(**values) for values in pending)
            db.commit()


def record_llm(db: Session, usage, *, user_id: str, project_id: str | None = None,
               revision: int | None = None, job_id: str | None = None,
               message_id: str | None = None) -> None:
    values = dict(
        user_id=user_id, project_id=project_id, revision=revision, job_id=job_id,
        message_id=message_id, kind="llm", provider=usage.provider, model=usage.model,
        task=usage.task, input_tokens=usage.input_tokens, output_tokens=usage.output_tokens,
        quantity=usage.input_tokens + usage.output_tokens, unit="tokens",
        cost_known=usage.model in LLM_PRICE_PER_MTOK,
        estimated_cost_usd=llm_cost(usage.model, usage.input_tokens, usage.output_tokens))
    if db.info.get("capture_provider_usage"):
        db.info.setdefault("provider_usage", []).append(values)
    else:
        db.add(UsageRecord(**values))


# Rough list prices (USD) used for the admin cost overview. Edit to match your contracts.
LLM_PRICE_PER_MTOK = {
    "claude-opus-5-5": (4.0, 20.0), "claude-sonnet-5-5": (2.0, 10.0), "claude-haiku-5-5": (0.10, 0.50),
    "gpt-4.1-mini": (0.40, 1.60), "gemini-2.5-flash": (0.30, 2.50),
}


def llm_cost(model: str | None, inp: int, out: int) -> float:
    pin, pout = LLM_PRICE_PER_MTOK.get(model or "", (0.0, 0.0))
    return (inp * pin + out * pout) / 1_000_000
