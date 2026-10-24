from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import current_user, rate_limit
from app.api.serializers import project_detail, project_summary
from app.config import get_settings
from app.conversation.engine import retry_failed_job
from app.db import get_db
from app.models import GeneratedAsset, User, VideoProject
from app.security import sign_asset_url, verify_asset_signature
from app.storage import LocalStorage, get_storage

router = APIRouter(tags=["projects"])


def _owned_project(db: Session, user: User, project_id: str) -> VideoProject:
    p = db.get(VideoProject, project_id)
    # 404 (not 403) so other users' project ids aren't confirmed to exist.
    if p is None or p.user_id != user.id:
        raise HTTPException(404, "Project not found")
    return p


@router.get("/api/projects")
def list_projects(limit: int = Query(50, le=200), offset: int = Query(0, ge=0),
                  user: User = Depends(current_user), db: Session = Depends(get_db)):
    rows = db.scalars(select(VideoProject).where(VideoProject.user_id == user.id)
                      .order_by(VideoProject.created_at.desc()).limit(limit).offset(offset)).all()
    return {"items": [project_summary(db, p) for p in rows]}


@router.get("/api/projects/{project_id}")
def get_project(project_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    return project_detail(db, _owned_project(db, user, project_id))


@router.post("/api/projects/{project_id}/retry", dependencies=[Depends(rate_limit("retry", 10, 600))])
def retry_project(project_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    p = _owned_project(db, user, project_id)
    res = retry_failed_job(db, p, actor_id=user.id)
    if isinstance(res, str):
        msg = {"already_running": "This video is already generating",
               "insufficient_credits": "Not enough credits",
               "nothing_to_retry": "Nothing to retry"}[res]
        raise HTTPException(409 if res != "insufficient_credits" else 402, msg)
    db.commit()
    return {"ok": True, "job_id": res.id}


@router.get("/api/assets/{asset_id}/link")
def fresh_link(asset_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    a = db.get(GeneratedAsset, asset_id)
    if a is None or a.user_id != user.id or a.deleted_at:
        raise HTTPException(404, "File not found")
    return {"url": sign_asset_url(a.id)}


@router.get("/api/files/{asset_id}")
def download(asset_id: str, request: Request, exp: int = Query(...), sig: str = Query(..., max_length=100),
             db: Session = Depends(get_db)):
    """Signed, expiring download (works from WhatsApp without a website session)."""
    problem = verify_asset_signature(asset_id, exp, sig)
    if problem == "expired":
        return JSONResponse(status_code=410, content={
            "detail": "This download link has expired. Reply 'link' in WhatsApp or open your dashboard "
                      f"({get_settings().web_base_url}/dashboard) for a new one."})
    if problem:
        raise HTTPException(403, "Invalid link")
    a = db.get(GeneratedAsset, asset_id)
    if a is None or a.deleted_at:
        raise HTTPException(404, "This file is no longer available")
    storage = get_storage()
    ext = {"video/mp4": "mp4", "application/json": "json", "application/x-subrip": "srt"}.get(a.mime_type, "bin")
    filename = f"reelchat-{a.id[:8]}.{ext}"
    direct = storage.presigned_url(a.storage_key, 600, filename)
    if direct:
        return RedirectResponse(direct, status_code=302)
    if not isinstance(storage, LocalStorage):
        raise HTTPException(500, "storage misconfigured")
    path = storage.path(a.storage_key)
    if not path.exists():
        raise HTTPException(404, "This file is no longer available")
    inline = request.query_params.get("inline") == "1"
    return FileResponse(path, media_type=a.mime_type, filename=None if inline else filename,
                        headers={"Cache-Control": "private, max-age=600"})


@router.get("/api/pipeline-files/{token}")
def pipeline_file(token: str):
    """Signed, expiring access to a work file for external pipeline agents (HTTP steps)."""
    from pathlib import Path

    from app.security import TokenError, verify_payload

    try:
        data = verify_payload(token, "pipeline-file")
    except TokenError as e:
        raise HTTPException(410 if e.reason == "expired" else 403, "Invalid or expired link") from e
    s = get_settings()
    path = Path(str(data.get("p", ""))).resolve()
    roots = [Path(s.work_dir).resolve(), Path(s.storage_local_dir).resolve(), Path(s.music_library_dir).resolve()]
    if not any(r == path or r in path.parents for r in roots) or not path.is_file():
        raise HTTPException(404, "Not found")
    return FileResponse(path)
