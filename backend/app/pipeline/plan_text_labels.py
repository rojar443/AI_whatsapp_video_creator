"""Stable human labels for source files: "clip 1", "photo 2" (in upload order)."""
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import ProjectAsset


def asset_labels(db: Session, project_id: str) -> dict[str, str]:
    counts = {"video": 0, "photo": 0}
    out = {}
    for a in db.scalars(select(ProjectAsset).where(ProjectAsset.project_id == project_id)
                        .order_by(ProjectAsset.order_index, ProjectAsset.created_at)).all():
        counts[a.kind] = counts.get(a.kind, 0) + 1
        out[a.id] = f"{'clip' if a.kind == 'video' else 'photo'} {counts[a.kind]}"
    return out
