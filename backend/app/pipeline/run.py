"""Pipeline orchestration: planning (before confirmation) and rendering (after)."""
from __future__ import annotations

import hashlib
import json
import logging
import shutil
from datetime import timedelta
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app import playbooks, providers
from app.agents.base import USAGE_SINK
from app.config import get_settings
from app.conversation import preferences as P
from app.media import ffmpeg
from app.models import GeneratedAsset, GenerationJob, ProjectAsset, User, VideoPreferences, VideoProject, utcnow
from app.pipeline import contracts as C
from app.pipeline.plan_text_labels import asset_labels
from app.pipeline.registry import run_step
from app.services import credits
from app.storage import get_storage

log = logging.getLogger(__name__)
RES = {"standard": (720, 1280), "high": (1080, 1920)}
PHOTO_SECONDS = 3.0
FLAG_TEXT = {"dark": "too dark", "shaky": "too shaky", "duplicate": "near-duplicate", "blurry": "blurry",
             "too_short": "too short"}


def work_dir(name: str) -> Path:
    p = Path(get_settings().work_dir) / name
    p.mkdir(parents=True, exist_ok=True)
    return p


def source_assets(db: Session, project: VideoProject, wd: Path) -> list[C.SourceAsset]:
    storage = get_storage()
    out = []
    src_dir = wd / "src"
    src_dir.mkdir(exist_ok=True)
    rows = db.scalars(select(ProjectAsset).where(ProjectAsset.project_id == project.id,
                                                 ProjectAsset.status == "ready")
                      .order_by(ProjectAsset.order_index)).all()
    for a in rows:
        path = storage.local_path(a.storage_key, str(src_dir))
        out.append(C.SourceAsset(asset_id=a.id, kind=a.kind, order_index=a.order_index, file_path=path,
                                 duration=a.duration_seconds, width=a.width or 0, height=a.height or 0,
                                 has_audio=a.has_audio, caption=a.caption))
    return out


def edit_context(prefs: P.Prefs, target: float) -> C.EditContext:
    pb = playbooks.get(prefs.get("genre"))
    return C.EditContext(
        genre=pb.genre, vibe=prefs.get("vibe"), style=prefs.get("style") or pb.visual_treatment.grade,
        format=prefs.get("format") or "instagram_reel", duration=prefs.get("duration") or "30", target_seconds=target,
        instructions=prefs.get("instructions"), narration=prefs.get("narration") or "default",
        music=prefs.get("music") or "licensed", captions=prefs.get("captions") is not False,
        language=prefs.get("language") or "english", title_text=prefs.get("title_text"),
        playbook=pb.model_dump(mode="json"))


def _record_usage(db: Session, user_id: str, sink: list, job_id: str | None = None,
                  project_id: str | None = None, revision: int | None = None) -> None:
    for u in sink:
        credits.record_llm(db, u, user_id=user_id, job_id=job_id, project_id=project_id, revision=revision)


# ====================================================================== planning
def build_plan(db: Session, project: VideoProject, row: VideoPreferences) -> dict:
    prefs = P.Prefs.from_row(row)
    wd = work_dir(f"plan-{project.id}-r{row.revision}")
    assets = source_assets(db, project, wd)
    if not assets:
        raise ValueError("no usable clips or photos")
    used: dict[str, str] = {}
    sink: list = []
    token = USAGE_SINK.set(sink)
    try:
        pre_ctx = edit_context(prefs, 30)
        quality: C.QualityOutput = run_step("quality_filter", C.QualityInput(assets=assets, context=pre_ctx), used)
        keep_ids = {q.asset_id for q in quality.results if q.keep}
        kept = [a for a in assets if a.asset_id in keep_ids]
        available = sum((a.duration or 0) if a.kind == "video" else PHOTO_SECONDS * 1.4 for a in kept)
        ctx = edit_context(prefs, P.target_seconds(prefs, available))
        scenes: C.SceneSplitOutput = run_step("scene_split", C.SceneSplitInput(assets=kept, context=ctx,
                                                                                work_dir=str(wd)), used)
        scoring: C.ScoringOutput = run_step("moment_scoring", C.ScoringInput(segments=scenes.segments, context=ctx), used)
        seq: C.SequencingOutput = run_step("sequencing", C.SequencingInput(
            segments=scenes.segments, scores=scoring.scores, assets=kept, context=ctx), used)
        if not seq.items:
            raise ValueError("couldn't find usable moments")
        script: C.ScriptOutput = run_step("script", C.ScriptInput(items=seq.items, total_seconds=seq.total_seconds,
                                                                  context=ctx), used)
        music = None
        if ctx.music == "licensed":
            pb = ctx.playbook.get("audio", {})
            music = run_step("music", C.MusicInput(moods=list(pb.get("music_moods") or []),
                                                   energy=pb.get("music_energy", "medium"),
                                                   seconds=seq.total_seconds, work_dir=str(wd)), used)
    finally:
        USAGE_SINK.reset(token)
        _record_usage(db, project.user_id, sink, project_id=project.id, revision=row.revision)

    for q in quality.results:  # surface quality results on the uploads (dashboard)
        row_asset = db.get(ProjectAsset, q.asset_id)
        if row_asset is not None and row_asset.project_id == project.id:
            row_asset.quality_score = q.score
            row_asset.quality_flags = {"flags": q.flags, "duplicate_of": q.duplicate_of, "keep": q.keep}
    labels = asset_labels(db, project.id)
    dropped = [{"asset_id": q.asset_id, "label": labels.get(q.asset_id, "a clip"),
                "reason": ", ".join(FLAG_TEXT.get(f, f) for f in q.flags) or q.reason or "low quality"}
               for q in quality.results if not q.keep]
    for a in db.scalars(select(ProjectAsset).where(ProjectAsset.project_id == project.id,
                                                   ProjectAsset.status == "rejected")).all():
        dropped.append({"asset_id": a.id, "label": labels.get(a.id, "a file"), "reason": a.reject_reason or "unusable"})
    pb = playbooks.get(prefs.get("genre"))
    return {
        "items": [i.model_dump() for i in seq.items],
        "total_seconds": seq.total_seconds,
        "candidates": len(scenes.segments),
        "notes": seq.notes,
        "dropped": dropped,
        "labels": labels,
        "script": script.model_dump(),
        "music_title": (f"{music.title}" + (f" by {music.artist}" if music.artist else "")) if music else None,
        "music": music.model_dump() if music and not music.generated else None,
        "scorer": scoring.scorer,
        "pipeline": used,
        "playbook": f"{pb.genre}@{pb.version}",
        "quality": {q.asset_id: q.model_dump() for q in quality.results},
    }


# ====================================================================== render
class RenderFailure(Exception):
    def __init__(self, message: str, code: str = "render_failed", retryable: bool = False):
        super().__init__(message)
        self.code = code
        self.retryable = retryable


def item_starts(items: list[C.PlanItem], ctx: C.EditContext) -> list[float]:
    vt = ctx.playbook.get("visual_treatment", {})
    overlap = float(vt.get("transition_seconds", 0)) if vt.get("transition") == "fade" else 0.0
    starts, t = [], 0.0
    for it in items:
        starts.append(round(t, 3))
        t += (it.source_end - it.source_start) - overlap
    return starts


def fit_narration(path: str, max_seconds: float, words: list[C.WordTiming], wd: Path) -> tuple[str, list[C.WordTiming]]:
    d = ffmpeg.duration(path)
    if d <= max_seconds or d <= 0:
        return path, words
    factor = min(1.3, d / max_seconds)
    out = str(wd / "narration_fit.wav")
    ffmpeg.run(["-i", path, "-af", f"atempo={factor:.4f}", out])
    return out, [C.WordTiming(word=w.word, start=w.start / factor, end=w.end / factor) for w in words]


def render_job(db: Session, job: GenerationJob, on_stage) -> dict:
    s = get_settings()
    project = db.get(VideoProject, job.project_id)
    row = db.scalar(select(VideoPreferences).where(VideoPreferences.project_id == project.id,
                                                   VideoPreferences.revision == job.revision))
    plan = row.plan or {}
    prefs = P.Prefs.from_row(row)
    wd = work_dir(f"job-{job.id}-{job.run_number}-{job.attempts}")
    assets = source_assets(db, project, wd)
    items = [C.PlanItem.model_validate(i) for i in plan.get("items", [])]
    if not items:
        raise RenderFailure("plan has no moments", code="empty_plan")
    total = float(plan["total_seconds"])
    ctx = edit_context(prefs, total)
    w, h = RES.get(prefs.get("quality") or "standard", RES["standard"])
    used: dict[str, str] = dict(plan.get("pipeline") or {})
    demo: list[str] = []
    if plan.get("scorer") == "heuristic":
        demo.append("heuristic moment selection")
    sink: list = []
    token = USAGE_SINK.set(sink)
    try:
        script = C.ScriptOutput.model_validate(plan.get("script") or {"title": project.title, "lines": []})
        narration_path, words, offset = None, [], 0.3
        if ctx.narration != "none" and script.full_text:
            on_stage("narration")
            narr: C.NarrationOutput = run_step("narration", C.NarrationInput(
                text=script.full_text, vibe=ctx.vibe, language=ctx.language, work_dir=str(wd)), used)
            narration_path, words = fit_narration(narr.audio_path, total - offset - 0.4, narr.words, wd)
            if narr.is_demo:
                demo.append(f"{narr.provider} voice")
        subs = None
        if ctx.captions:
            on_stage("captions")
            cap: C.CaptionsOutput = run_step("captions", C.CaptionsInput(
                script=script, words=words, narration_offset=offset, items=items,
                item_starts=item_starts(items, ctx), total_seconds=total,
                position=ctx.playbook.get("visual_treatment", {}).get("caption_position", "lower_third"),
                work_dir=str(wd)), used)
            subs = cap.subtitles_path
        music_path = None
        if ctx.music == "licensed":
            stored = plan.get("music") or {}
            if stored.get("audio_path") and Path(stored["audio_path"]).exists():
                music_path = stored["audio_path"]
            else:
                pb = ctx.playbook.get("audio", {})
                m: C.MusicOutput = run_step("music", C.MusicInput(moods=list(pb.get("music_moods") or []),
                                                                  energy=pb.get("music_energy", "medium"),
                                                                  seconds=total, work_dir=str(wd)), used)
                music_path = m.audio_path
        on_stage("render")
        out: C.RenderOutput = run_step("render", C.RenderInput(
            items=items, assets=assets, context=ctx, narration_path=narration_path, narration_offset=offset,
            subtitles_path=subs, music_path=music_path, title=prefs.get("title_text") or script.title,
            width=w, height=h, target_bytes=s.delivery_target_bytes, work_dir=str(wd)), used)
    finally:
        USAGE_SINK.reset(token)
        _record_usage(db, job.user_id, sink, job.id, project.id, job.revision)

    on_stage("validation")
    report = ffmpeg.validate_mp4(out.video_path, w, h, total, tolerance=1.5)
    if not report.ok:
        raise RenderFailure("output validation failed: " + "; ".join(report.problems), code="invalid_output")

    storage = get_storage()
    owner = db.get(User, job.user_id)
    days = s.anon_retention_days if owner and owner.is_anonymous else s.asset_retention_days
    expires = utcnow() + timedelta(days=days)
    base = f"users/{job.user_id}/projects/{project.id}/out/r{job.revision}-{job.id[:8]}-{job.run_number}"
    video_key = base + ".mp4"
    size = storage.put_file(video_key, out.video_path, "video/mp4")
    sha = hashlib.sha256(Path(out.video_path).read_bytes()).hexdigest()
    asset = GeneratedAsset(user_id=job.user_id, project_id=project.id, job_id=job.id, kind="video",
                           storage_key=video_key, mime_type="video/mp4", size_bytes=size,
                           duration_seconds=report.duration, width=w, height=h, checksum_sha256=sha,
                           is_demo=bool(demo), expires_at=expires,
                           extra={"title": prefs.get("title_text") or script.title, "demo": demo,
                                  "playbook": plan.get("playbook"), "music": plan.get("music_title")})
    db.add(asset)
    script_bytes = json.dumps(script.model_dump(), ensure_ascii=False, indent=2).encode()
    storage.put_bytes(base + ".script.json", script_bytes, "application/json")
    db.add(GeneratedAsset(user_id=job.user_id, project_id=project.id, job_id=job.id, kind="script",
                          storage_key=base + ".script.json", mime_type="application/json",
                          size_bytes=len(script_bytes), expires_at=expires))
    if subs:
        n = storage.put_file(base + ".ass", subs, "text/plain")
        db.add(GeneratedAsset(user_id=job.user_id, project_id=project.id, job_id=job.id, kind="subtitles",
                              storage_key=base + ".ass", mime_type="text/plain", size_bytes=n, expires_at=expires))
    job.pipeline = used
    db.flush()
    shutil.rmtree(wd, ignore_errors=True)
    return {"asset": asset, "title": prefs.get("title_text") or script.title, "demo": demo}
