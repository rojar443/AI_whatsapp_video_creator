from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.conversation import requirements as req
from app.models import (
    GeneratedAsset,
    GenerationJob,
    User,
    VideoPreferences,
    VideoProject,
    WhatsAppAccount,
    aware,
)
from app.security import sign_asset_url


def iso(dt) -> str | None:
    dt = aware(dt)
    return dt.isoformat() if dt else None


def user_out(db: Session, user: User) -> dict[str, Any]:
    wa = db.scalar(select(WhatsAppAccount).where(WhatsAppAccount.user_id == user.id))
    pending = db.scalar(select(WhatsAppAccount).where(WhatsAppAccount.pending_user_id == user.id))
    s = get_settings()
    return {
        "id": user.id,
        "name": user.name,
        "email": user.email,
        "phone": user.phone_e164,
        "role": user.role,
        "email_verified": user.email_verified_at is not None,
        "credits": user.credits_balance,
        "created_at": iso(user.created_at),
        "whatsapp": {
            "status": "linked" if wa else ("pending" if pending else "not_linked"),
            "number": ("+" + wa.wa_id) if wa else None,
            "linked_at": iso(wa.linked_at) if wa else None,
            "business_number": s.whatsapp_business_number,
        },
    }


def prefs_out(row: VideoPreferences | None) -> dict[str, Any] | None:
    if row is None:
        return None
    values = {f: getattr(row, f) for f in req.ALL_FIELDS if f != "user_script"}
    return {"revision": row.revision, "values": values, "sources": row.field_sources or {},
            "credits_cost": req.credits_for(row.duration_seconds, row.quality)}


def job_out(job: GenerationJob) -> dict[str, Any]:
    return {
        "id": job.id, "revision": job.revision, "status": job.status, "stage": job.stage,
        "attempts": job.attempts, "run_number": job.run_number, "credits_charged": job.credits_charged,
        "provider": job.video_provider, "is_mock": job.is_mock, "error_code": job.error_code,
        "error_message": job.error_message, "created_at": iso(job.created_at),
        "started_at": iso(job.started_at), "finished_at": iso(job.finished_at),
    }


def asset_out(a: GeneratedAsset, with_url: bool = True) -> dict[str, Any]:
    return {
        "id": a.id, "kind": a.kind, "job_id": a.job_id, "mime_type": a.mime_type, "size_bytes": a.size_bytes,
        "duration_seconds": a.duration_seconds, "width": a.width, "height": a.height, "is_mock": a.is_mock,
        "title": (a.extra or {}).get("title"), "created_at": iso(a.created_at), "expires_at": iso(a.expires_at),
        "url": sign_asset_url(a.id, 3600) if with_url and not a.deleted_at else None,
        "deleted": a.deleted_at is not None,
    }


def project_summary(db: Session, p: VideoProject) -> dict[str, Any]:
    latest_video = db.scalar(select(GeneratedAsset).where(
        GeneratedAsset.project_id == p.id, GeneratedAsset.kind == "video", GeneratedAsset.deleted_at.is_(None)
    ).order_by(GeneratedAsset.created_at.desc()).limit(1))
    return {
        "id": p.id, "title": p.title, "status": p.status, "revision": p.revision, "last_error": p.last_error,
        "created_at": iso(p.created_at), "updated_at": iso(p.updated_at),
        "video": asset_out(latest_video) if latest_video else None,
    }


def project_detail(db: Session, p: VideoProject) -> dict[str, Any]:
    prefs = db.scalars(select(VideoPreferences).where(VideoPreferences.project_id == p.id)
                       .order_by(VideoPreferences.revision)).all()
    jobs = db.scalars(select(GenerationJob).where(GenerationJob.project_id == p.id)
                      .order_by(GenerationJob.created_at)).all()
    assets = db.scalars(select(GeneratedAsset).where(GeneratedAsset.project_id == p.id)
                        .order_by(GeneratedAsset.created_at)).all()
    out = project_summary(db, p)
    out.update(
        preferences=[prefs_out(r) for r in prefs],
        jobs=[job_out(j) for j in jobs],
        assets=[asset_out(a) for a in assets],
        can_retry=bool(jobs) and jobs[-1].status == "failed" and jobs[-1].revision == p.revision,
    )
    return out
