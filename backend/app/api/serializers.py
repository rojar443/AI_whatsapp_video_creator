from __future__ import annotations

from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.conversation import preferences as P
from app.models import GeneratedAsset, GenerationJob, ProjectAsset, User, VideoPreferences, VideoProject, WhatsAppAccount, aware
from app.pipeline.plan_text_labels import asset_labels
from app.security import sign_asset_url


def iso(dt) -> str | None:
    dt = aware(dt)
    return dt.isoformat() if dt else None


def user_out(db: Session, user: User) -> dict[str, Any]:
    wa = db.scalar(select(WhatsAppAccount).where(WhatsAppAccount.user_id == user.id, WhatsAppAccount.status == "linked",
                                                 WhatsAppAccount.wa_id.not_like("tg:%")))
    tg = db.scalar(select(WhatsAppAccount).where(WhatsAppAccount.user_id == user.id, WhatsAppAccount.status == "linked",
                                                 WhatsAppAccount.wa_id.like("tg:%")))
    pending = db.scalar(select(WhatsAppAccount).where(WhatsAppAccount.pending_user_id == user.id))
    return {
        "id": user.id, "name": user.name, "email": user.email, "phone": user.phone_e164,
        "email_verified": user.email_verified_at is not None, "credits": user.credits_balance,
        "created_at": iso(user.created_at),
        "whatsapp": {
            "enabled": get_settings().whatsapp_enabled,
            "status": "linked" if wa else ("pending" if pending else "not_linked"),
            "number": ("+" + wa.wa_id) if wa else None,
            "linked_at": iso(wa.linked_at) if wa else None,
            "business_number": get_settings().whatsapp_business_number,
        },
        "telegram": {"enabled": bool(get_settings().telegram_bot_token), "linked": tg is not None,
                     "linked_at": iso(tg.linked_at) if tg else None},
    }


def job_out(job: GenerationJob) -> dict[str, Any]:
    return {"id": job.id, "revision": job.revision, "status": job.status, "stage": job.stage, "attempts": job.attempts,
            "run_number": job.run_number, "credits_charged": job.credits_charged, "pipeline": job.pipeline,
            "error_code": job.error_code, "error_message": job.error_message, "created_at": iso(job.created_at),
            "started_at": iso(job.started_at), "finished_at": iso(job.finished_at)}


def asset_out(a: GeneratedAsset) -> dict[str, Any]:
    return {"id": a.id, "kind": a.kind, "job_id": a.job_id, "mime_type": a.mime_type, "size_bytes": a.size_bytes,
            "duration_seconds": a.duration_seconds, "width": a.width, "height": a.height, "is_demo": a.is_demo,
            "title": (a.extra or {}).get("title"), "demo": (a.extra or {}).get("demo"),
            "created_at": iso(a.created_at), "expires_at": iso(a.expires_at),
            "url": sign_asset_url(a.id, 3600) if not a.deleted_at else None, "deleted": a.deleted_at is not None}


def source_out(a: ProjectAsset, label: str) -> dict[str, Any]:
    return {"id": a.id, "label": label, "kind": a.kind, "status": a.status, "reject_reason": a.reject_reason,
            "duration_seconds": a.duration_seconds, "width": a.width, "height": a.height,
            "quality_flags": a.quality_flags, "created_at": iso(a.created_at)}


def prefs_out(row: VideoPreferences) -> dict[str, Any]:
    plan = row.plan or {}
    return {"revision": row.revision, "values": {f: getattr(row, f) for f in P.ALL_FIELDS},
            "sources": row.field_sources or {}, "playbook": row.playbook_version,
            "plan": {k: plan.get(k) for k in ("items", "total_seconds", "dropped", "notes", "script", "music_title",
                                              "scorer", "pipeline", "labels")} if plan else None}


def project_summary(db: Session, p: VideoProject) -> dict[str, Any]:
    video = db.scalar(select(GeneratedAsset).where(GeneratedAsset.project_id == p.id, GeneratedAsset.kind == "video",
                                                   GeneratedAsset.deleted_at.is_(None))
                      .order_by(GeneratedAsset.created_at.desc()).limit(1))
    n_src = db.scalar(select(func.count(ProjectAsset.id)).where(ProjectAsset.project_id == p.id))
    return {"id": p.id, "title": p.title, "status": p.status, "stage": p.stage, "revision": p.revision,
            "last_error": p.last_error, "sources": n_src, "created_at": iso(p.created_at),
            "updated_at": iso(p.updated_at), "video": asset_out(video) if video else None}


def project_detail(db: Session, p: VideoProject) -> dict[str, Any]:
    prefs = db.scalars(select(VideoPreferences).where(VideoPreferences.project_id == p.id)
                       .order_by(VideoPreferences.revision)).all()
    jobs = db.scalars(select(GenerationJob).where(GenerationJob.project_id == p.id).order_by(GenerationJob.created_at)).all()
    assets = db.scalars(select(GeneratedAsset).where(GeneratedAsset.project_id == p.id)
                        .order_by(GeneratedAsset.created_at)).all()
    labels = asset_labels(db, p.id)
    sources = db.scalars(select(ProjectAsset).where(ProjectAsset.project_id == p.id)
                         .order_by(ProjectAsset.order_index)).all()
    out = project_summary(db, p)
    out.update(preferences=[prefs_out(r) for r in prefs], jobs=[job_out(j) for j in jobs],
               assets=[asset_out(a) for a in assets], source_files=[source_out(a, labels.get(a.id, "")) for a in sources],
               can_retry=bool(jobs) and jobs[-1].status == "failed" and jobs[-1].revision == p.revision)
    return out
