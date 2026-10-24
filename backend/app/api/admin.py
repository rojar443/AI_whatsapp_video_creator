"""Admin area: everyone who has used the app (Telegram, WhatsApp or website), with a few safe actions.

Only account and usage data is returned: never message text or media."""
from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy import exists, func, or_, select
from sqlalchemy.orm import Session

from app.api.deps import client_ip, require_admin
from app.api.serializers import iso
from app.db import get_db
from app.models import ProjectStatus, User, VideoProject, WhatsAppAccount, aware, utcnow
from app.services import credits
from app.services.audit import audit

router = APIRouter(prefix="/api/admin", tags=["admin"])


def _channel(wa: WhatsAppAccount) -> dict:
    tg = wa.wa_id.startswith("tg:")
    return {"kind": "telegram" if tg else "whatsapp", "id": wa.wa_id[3:] if tg else wa.wa_id,
            "name": wa.profile_name, "status": wa.status}


@router.get("/users")
def list_users(q: str | None = Query(None, max_length=100), limit: int = Query(25, ge=1, le=100),
               offset: int = Query(0, ge=0), admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    last_in = (select(WhatsAppAccount.user_id.label("uid"), func.max(WhatsAppAccount.last_inbound_at).label("seen"))
               .where(WhatsAppAccount.user_id.is_not(None)).group_by(WhatsAppAccount.user_id).subquery())
    seen = func.coalesce(last_in.c.seen, User.updated_at)
    stmt = select(User, seen.label("seen")).outerjoin(last_in, last_in.c.uid == User.id)
    count = select(func.count(User.id))
    if q:
        like = f"%{q.lower()}%"
        cond = or_(func.lower(User.name).like(like), func.lower(func.coalesce(User.email, "")).like(like),
                   exists().where(WhatsAppAccount.user_id == User.id,
                                  or_(func.lower(func.coalesce(WhatsAppAccount.profile_name, "")).like(like),
                                      WhatsAppAccount.wa_id.like(like))))
        stmt, count = stmt.where(cond), count.where(cond)
    rows = db.execute(stmt.order_by(seen.desc(), User.created_at.desc()).limit(limit).offset(offset)).all()
    items = []
    for u, last_seen in rows:
        chans = db.scalars(select(WhatsAppAccount).where(WhatsAppAccount.user_id == u.id)).all()
        projects = db.scalar(select(func.count(VideoProject.id)).where(VideoProject.user_id == u.id)) or 0
        reels = db.scalar(select(func.count(VideoProject.id)).where(
            VideoProject.user_id == u.id, VideoProject.status == ProjectStatus.completed.value)) or 0
        channels = [_channel(w) for w in chans] or ([{"kind": "web", "id": None, "name": None, "status": "registered"}]
                                                    if u.email else [])
        items.append({
            "id": u.id, "name": u.name or (chans[0].profile_name if chans else "") or "", "email": u.email,
            "channels": channels, "registered": not u.is_anonymous, "role": u.role, "status": u.status,
            "credits": u.credits_balance, "projects": projects, "reels": reels,
            "first_seen": iso(u.created_at), "last_seen": iso(aware(last_seen)),
        })
    return {"items": items, "total": db.scalar(count) or 0, "limit": limit, "offset": offset}


class UserPatch(BaseModel):
    status: Literal["active", "suspended"] | None = None
    credits_delta: int | None = Field(default=None, ge=-1000, le=1000)


@router.patch("/users/{user_id}")
def update_user(user_id: str, body: UserPatch, request: Request, admin: User = Depends(require_admin),
                db: Session = Depends(get_db)):
    u = db.get(User, user_id)
    if u is None:
        raise HTTPException(404, "User not found")
    if u.id == admin.id and body.status == "suspended":
        raise HTTPException(400, "You can't suspend yourself")
    changes = body.model_dump(exclude_none=True)
    if body.status:
        u.status = body.status
    if body.credits_delta:
        if u.credits_balance + body.credits_delta < 0:
            raise HTTPException(400, "Balance can't go negative")
        credits.grant(db, u, body.credits_delta, kind="credit_adjust")
    audit(db, "admin.user_updated", actor_id=admin.id, target_type="user", target_id=u.id, details=changes,
          ip=client_ip(request))
    db.commit()
    return {"ok": True, "status": u.status, "credits": u.credits_balance, "at": iso(utcnow())}
