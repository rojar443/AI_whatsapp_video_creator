"""Anonymous WhatsApp profiles and merging them into a website account."""
from __future__ import annotations

from datetime import timedelta

from sqlalchemy import update
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import (
    Conversation,
    GeneratedAsset,
    GenerationJob,
    Message,
    ProjectAsset,
    UsageRecord,
    User,
    VideoProject,
    VoiceProfile,
    WhatsAppAccount,
    utcnow,
)
from app.security import normalize_phone
from app.services import credits
from app.services.audit import audit


def create_anonymous(db: Session, wa: WhatsAppAccount) -> User:
    s = get_settings()
    user = User(is_anonymous=True, name=(wa.profile_name or "")[:120], phone_e164=normalize_phone("+" + wa.wa_id),
                credits_balance=0)
    db.add(user)
    db.flush()
    credits.grant(db, user, s.anon_credits, key=f"anon-signup:{user.id}")
    wa.user_id = user.id
    wa.status = "anonymous"
    audit(db, "user.anonymous_created", actor_id=user.id, target_type="whatsapp_account", target_id=wa.id)
    return user


def merge_anonymous(db: Session, anon: User, user: User) -> int:
    """Move an anonymous profile's projects and history to a registered account."""
    if anon.id == user.id or not anon.is_anonymous:
        return 0
    moved = 0
    for model in (VideoProject, ProjectAsset, GeneratedAsset, GenerationJob, Message, VoiceProfile, Conversation):
        res = db.execute(update(model).where(model.user_id == anon.id).values(user_id=user.id))
        if model is VideoProject:
            moved = res.rowcount or 0
    # Cost rows follow the projects; the anonymous credit ledger is dropped with the profile
    # (the registered account has its own balance).
    db.execute(update(UsageRecord).where(UsageRecord.user_id == anon.id, UsageRecord.credits_delta == 0)
               .values(user_id=user.id))
    # Registered retention applies from now on.
    keep_until = utcnow() + timedelta(days=get_settings().asset_retention_days)
    db.execute(update(VideoProject).where(VideoProject.user_id == user.id, VideoProject.expires_at.is_not(None))
               .values(expires_at=keep_until))
    db.execute(update(GeneratedAsset).where(GeneratedAsset.user_id == user.id, GeneratedAsset.deleted_at.is_(None))
               .values(expires_at=keep_until))
    db.flush()
    db.delete(anon)
    audit(db, "user.anonymous_merged", actor_id=user.id, target_type="user", target_id=anon.id,
          details={"projects": moved})
    return moved
