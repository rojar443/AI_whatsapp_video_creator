"""Credit ledger. Balance changes and their ledger rows commit together; idempotency keys
make reserve/refund safe to call twice (e.g. on a retried task)."""
from __future__ import annotations

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.models import GenerationJob, UsageRecord, User


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


# Rough list prices (USD) used for the admin cost overview. Edit to match your contracts.
LLM_PRICE_PER_MTOK = {
    "claude-opus-5-5": (4.0, 20.0), "claude-sonnet-5-5": (2.0, 10.0), "claude-haiku-5-5": (0.10, 0.50),
    "gpt-4.1-mini": (0.40, 1.60), "gemini-2.5-flash": (0.30, 2.50),
}


def llm_cost(model: str | None, inp: int, out: int) -> float:
    pin, pout = LLM_PRICE_PER_MTOK.get(model or "", (0.0, 0.0))
    return (inp * pin + out * pout) / 1_000_000
