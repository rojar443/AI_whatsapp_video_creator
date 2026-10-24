"""Admin area: everyone who has used the app (Telegram, WhatsApp or website), with a few safe actions.

Only account and usage data is returned: never message text or media."""
from __future__ import annotations

from datetime import timedelta
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy import and_, case, exists, func, or_, select
from sqlalchemy.orm import Session

from app.api.deps import client_ip, require_admin
from app.api.serializers import iso
from app.db import get_db
from app.models import Message, ProjectStatus, UsageRecord, User, VideoProject, WhatsAppAccount, aware, utcnow
from app.services import credits
from app.services.audit import audit

router = APIRouter(prefix="/api/admin", tags=["admin"])


def _channel(wa: WhatsAppAccount) -> dict:
    tg = wa.wa_id.startswith("tg:")
    return {"kind": "telegram" if tg else "whatsapp", "id": wa.wa_id[3:] if tg else wa.wa_id,
            "name": wa.profile_name, "status": wa.status}


@router.get("/users")
def list_users(q: str | None = Query(None, max_length=100),
               channel: Literal["telegram", "whatsapp", "web"] | None = Query(None),
               talked: bool = Query(False, description="only people who have sent the bot a message"), limit: int = Query(25, ge=1, le=100),
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
    if talked:
        cond = exists().where(Message.user_id == User.id, Message.direction == "inbound")
        stmt, count = stmt.where(cond), count.where(cond)
    if channel:
        if channel == "web":
            cond = User.email.is_not(None)
        else:
            like_tg = WhatsAppAccount.wa_id.like("tg:%")
            cond = exists().where(WhatsAppAccount.user_id == User.id, like_tg if channel == "telegram" else ~like_tg)
        stmt, count = stmt.where(cond), count.where(cond)
    rows = db.execute(stmt.order_by(seen.desc(), User.created_at.desc()).limit(limit).offset(offset)).all()
    ids = [u.id for u, _ in rows]
    usage = {uid: (tok or 0, usd or 0.0) for uid, tok, usd in db.execute(
        select(UsageRecord.user_id,
               func.sum(case((and_(UsageRecord.kind == "llm", UsageRecord.unit == "tokens"), UsageRecord.quantity), else_=0)),
               func.sum(UsageRecord.estimated_cost_usd))
        .where(UsageRecord.user_id.in_(ids)).group_by(UsageRecord.user_id)).all()}
    items = []
    for u, last_seen in rows:
        chans = db.scalars(select(WhatsAppAccount).where(WhatsAppAccount.user_id == u.id)).all()
        projects = db.scalar(select(func.count(VideoProject.id)).where(VideoProject.user_id == u.id)) or 0
        reels = db.scalar(select(func.count(VideoProject.id)).where(
            VideoProject.user_id == u.id, VideoProject.status == ProjectStatus.completed.value)) or 0
        messages = db.scalar(select(func.count(Message.id)).where(
            Message.user_id == u.id, Message.direction == "inbound")) or 0
        channels = [_channel(w) for w in chans] or ([{"kind": "web", "id": None, "name": None, "status": "registered"}]
                                                    if u.email else [])
        items.append({
            "id": u.id, "name": u.name or (chans[0].profile_name if chans else "") or "", "email": u.email,
            "channels": channels, "registered": not u.is_anonymous, "role": u.role, "status": u.status,
            "credits": u.credits_balance, "projects": projects, "reels": reels, "messages": messages,
            "tokens": int(usage.get(u.id, (0, 0))[0]), "cost_usd": round(float(usage.get(u.id, (0, 0))[1]), 4),
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


@router.get("/summary")
def summary(days: int = Query(30, ge=1, le=365), admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    """Totals for the admin header: people, who has talked to the bot, and token usage / estimated cost."""
    since = utcnow() - timedelta(days=days)
    llm = and_(UsageRecord.kind == "llm", UsageRecord.unit == "tokens")
    tokens, cost = db.execute(select(
        func.coalesce(func.sum(case((llm, UsageRecord.quantity), else_=0)), 0),
        func.coalesce(func.sum(UsageRecord.estimated_cost_usd), 0)).where(UsageRecord.created_at >= since)).one()
    by_provider = [{"provider": prov or "unknown", "kind": kind, "tokens": int(tok or 0), "cost_usd": round(float(usd or 0), 4)}
                   for prov, kind, tok, usd in db.execute(
                       select(UsageRecord.provider, UsageRecord.kind,
                              func.sum(case((llm, UsageRecord.quantity), else_=0)), func.sum(UsageRecord.estimated_cost_usd))
                       .where(UsageRecord.created_at >= since, UsageRecord.kind.not_like("credit%"))  # credits aren't provider usage
                       .group_by(UsageRecord.provider, UsageRecord.kind)).all()]
    talked = db.scalar(select(func.count(func.distinct(Message.user_id))).where(
        Message.direction == "inbound", Message.user_id.is_not(None))) or 0
    return {"days": days, "users": db.scalar(select(func.count(User.id))) or 0, "talked_to_bot": talked,
            "tokens": int(tokens), "cost_usd": round(float(cost), 4), "by_provider": by_provider}
