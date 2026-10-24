"""Admin API (role=admin). Every mutating action writes an audit log entry."""
from __future__ import annotations

from datetime import timedelta
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app import providers
from app.api.deps import client_ip, require_admin
from app.api.serializers import iso, job_out
from app.conversation.engine import retry_failed_job
from app.db import get_db
from app.models import (
    AuditLog,
    GenerationJob,
    ProviderConfiguration,
    TaskQueue,
    UsageRecord,
    User,
    VideoProject,
    WhatsAppAccount,
    utcnow,
)
from app.services import credits
from app.services.audit import audit

router = APIRouter(prefix="/api/admin", tags=["admin"])


@router.get("/users")
def list_users(q: str | None = Query(None, max_length=100), limit: int = Query(50, le=200),
               admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    stmt = select(User).order_by(User.created_at.desc()).limit(limit)
    if q:
        like = f"%{q.lower()}%"
        stmt = stmt.where(or_(func.lower(User.email).like(like), func.lower(User.name).like(like)))
    users = db.scalars(stmt).all()
    out = []
    for u in users:
        wa = db.scalar(select(WhatsAppAccount).where(WhatsAppAccount.user_id == u.id))
        jobs = db.scalar(select(func.count(GenerationJob.id)).where(GenerationJob.user_id == u.id))
        out.append({"id": u.id, "name": u.name, "email": u.email, "role": u.role, "status": u.status,
                    "credits": u.credits_balance, "email_verified": u.email_verified_at is not None,
                    "whatsapp_linked": wa is not None, "jobs": jobs, "created_at": iso(u.created_at)})
    return {"items": out}


class UserPatch(BaseModel):
    status: Literal["active", "suspended"] | None = None
    role: Literal["user", "admin"] | None = None
    credits_delta: int | None = Field(default=None, ge=-1000, le=1000)


@router.patch("/users/{user_id}")
def update_user(user_id: str, body: UserPatch, request: Request, admin: User = Depends(require_admin),
                db: Session = Depends(get_db)):
    u = db.get(User, user_id)
    if u is None:
        raise HTTPException(404, "User not found")
    if u.id == admin.id and (body.status == "suspended" or body.role == "user"):
        raise HTTPException(400, "You can't suspend or demote yourself")
    changes = body.model_dump(exclude_none=True)
    if body.status:
        u.status = body.status
    if body.role:
        u.role = body.role
    if body.credits_delta:
        if u.credits_balance + body.credits_delta < 0:
            raise HTTPException(400, "Balance can't go negative")
        credits.grant(db, u, body.credits_delta, kind="credit_adjust")
    audit(db, "admin.user_updated", actor_id=admin.id, target_type="user", target_id=u.id, details=changes,
          ip=client_ip(request))
    db.commit()
    return {"ok": True}


@router.get("/providers")
def get_providers(admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    return {
        "available": providers.CATEGORIES,
        "agent_tasks": ["default", "analysis", "response", "script"],
        "active": {c: providers.resolve(db, c) for c in providers.CATEGORIES},
    }


class ProviderUpdate(BaseModel):
    category: Literal["agent", "video", "tts", "transcription"]
    provider: str = Field(max_length=40)
    task: Literal["default", "analysis", "response", "script"] = "default"
    model: str | None = Field(default=None, max_length=100, pattern=r"^[A-Za-z0-9._:/@-]*$")
    reset: bool = False  # remove override, fall back to environment config


@router.put("/providers")
def set_provider(body: ProviderUpdate, request: Request, admin: User = Depends(require_admin),
                 db: Session = Depends(get_db)):
    if body.provider not in providers.CATEGORIES[body.category]:
        raise HTTPException(422, f"Unknown {body.category} provider")
    if body.task != "default" and body.category != "agent":
        raise HTTPException(422, "Per-task models apply to the agent only")
    row = db.scalar(select(ProviderConfiguration).where(
        ProviderConfiguration.category == body.category, ProviderConfiguration.task == body.task))
    if body.reset:
        if row:
            db.delete(row)
    else:
        if body.category == "agent" and body.task == "default":
            try:  # fail fast if credentials are missing
                providers.build_agent(body.provider, {"default": body.model})
            except Exception as e:  # noqa: BLE001
                raise HTTPException(422, f"Provider can't be used: {e}") from e
        if row is None:
            row = ProviderConfiguration(category=body.category, task=body.task, provider=body.provider)
            db.add(row)
        row.provider = body.provider
        row.model = body.model or None
        row.updated_by = admin.id
    audit(db, "admin.provider_changed", actor_id=admin.id, target_type="provider", target_id=body.category,
          details=body.model_dump(), ip=client_ip(request))
    db.commit()
    return {"active": providers.resolve(db, body.category)}


@router.get("/health")
def health(admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    queue = dict(db.execute(select(TaskQueue.status, func.count(TaskQueue.id)).group_by(TaskQueue.status)).all())
    return {"providers": providers.health_report(db), "queue": queue}


@router.get("/usage")
def usage(days: int = Query(30, ge=1, le=365), admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    since = utcnow() - timedelta(days=days)
    by_kind = db.execute(select(UsageRecord.kind, UsageRecord.provider, func.count(UsageRecord.id),
                                func.coalesce(func.sum(UsageRecord.quantity), 0),
                                func.coalesce(func.sum(UsageRecord.estimated_cost_usd), 0),
                                func.coalesce(func.sum(UsageRecord.credits_delta), 0))
                         .where(UsageRecord.created_at >= since)
                         .group_by(UsageRecord.kind, UsageRecord.provider)).all()
    jobs = dict(db.execute(select(GenerationJob.status, func.count(GenerationJob.id))
                           .where(GenerationJob.created_at >= since).group_by(GenerationJob.status)).all())
    return {
        "days": days,
        "by_kind": [{"kind": k, "provider": p, "count": c, "quantity": float(q), "cost_usd": float(cost),
                     "credits": int(cr)} for k, p, c, q, cost, cr in by_kind],
        "total_cost_usd": round(sum(float(r[4]) for r in by_kind), 4),
        "jobs": jobs,
        "users": db.scalar(select(func.count(User.id))),
    }


@router.get("/jobs")
def list_jobs(status: str | None = Query(None, max_length=20), limit: int = Query(50, le=200),
              admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    stmt = select(GenerationJob).order_by(GenerationJob.created_at.desc()).limit(limit)
    if status:
        stmt = stmt.where(GenerationJob.status == status)
    items = []
    for j in db.scalars(stmt).all():
        p = db.get(VideoProject, j.project_id)
        u = db.get(User, j.user_id)
        items.append(dict(job_out(j), project_id=j.project_id, project_title=p.title if p else None,
                          user_email=u.email if u else None))
    dead = db.scalars(select(TaskQueue).where(TaskQueue.status == "dead").order_by(TaskQueue.id.desc()).limit(20)).all()
    return {"items": items, "dead_tasks": [{"id": t.id, "type": t.task_type, "error": t.last_error,
                                            "attempts": t.attempts, "at": iso(t.finished_at)} for t in dead]}


@router.post("/jobs/{job_id}/retry")
def admin_retry(job_id: str, request: Request, admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    job = db.get(GenerationJob, job_id)
    if job is None:
        raise HTTPException(404, "Job not found")
    project = db.get(VideoProject, job.project_id)
    res = retry_failed_job(db, project, actor_id=admin.id)
    if isinstance(res, str):
        raise HTTPException(409, res.replace("_", " "))
    audit(db, "admin.job_retried", actor_id=admin.id, target_type="generation_job", target_id=job.id,
          ip=client_ip(request))
    db.commit()
    return {"ok": True}


@router.get("/audit-logs")
def audit_logs(limit: int = Query(100, le=500), action: str | None = Query(None, max_length=60),
               admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    stmt = select(AuditLog).order_by(AuditLog.created_at.desc()).limit(limit)
    if action:
        stmt = stmt.where(AuditLog.action == action)
    return {"items": [{"id": a.id, "action": a.action, "actor": a.actor_user_id, "target_type": a.target_type,
                       "target_id": a.target_id, "details": a.details, "ip": a.ip, "at": iso(a.created_at)}
                      for a in db.scalars(stmt).all()]}
