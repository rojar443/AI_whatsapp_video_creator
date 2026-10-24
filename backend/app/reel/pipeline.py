"""Wires the stages together from a config dict and writes report.json next to the reel."""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path

from app.reel.analyze import Analyzer, QualityAnalyzer, VisionAnalyzer
from app.reel.ingest import FolderIngestor, Ingestor
from app.reel.llm import VisionLLM, build_llm
from app.reel.models import Brief, ReelPlan, RenderResult, ShotScore, to_dict
from app.reel.render import FFmpegRenderer, Renderer
from app.reel.sample import FFmpegSceneSampler, Sampler
from app.reel.select import DirectorSelector, HeuristicSelector, Selector
from app.reel.timeline import TimelineSelector
from app.reel.tools import Session, session_dict

log = logging.getLogger(__name__)

DEFAULT_CONFIG = {
    "ingestor": "folder",
    "sampler": "ffmpeg_scene",
    "analyzers": ["quality"],
    "selector": "timeline",  # model returns timestamped cuts; heuristic | director also available
    "renderer": "ffmpeg",
    "llm": {"provider": "gemini", "model": None},
}


@dataclass
class Pipeline:
    ingestor: Ingestor
    sampler: Sampler
    analyzers: list[Analyzer]
    selector: Selector
    renderer: Renderer
    events: list[str] = field(default_factory=list)

    def _log(self, msg: str) -> None:
        self.events.append(msg)
        log.info(msg)

    def analyse(self, source: str, brief: Brief, workdir: str) -> Session:
        assets = self.ingestor.load(source)
        if not assets:
            raise ValueError(f"no usable videos or photos in {source}")
        shots = self.sampler.sample(assets, workdir)
        session = Session(brief, assets, shots, {s.id: ShotScore(s.id) for s in shots}, workdir)
        for skipped, why in getattr(self.ingestor, "skipped", []):
            session.notes.append(f"skipped {skipped}: {why}")
        self._log(f"ingest: {len(assets)} assets -> {len(shots)} shots")
        for a in self.analyzers:
            try:
                a.analyze(shots, session.scores, brief)
                self._log(f"analyze[{a.name}]: {len(session.candidates())} candidates")
            except Exception as e:  # noqa: BLE001 - a failed model analyzer must not stop the reel
                if isinstance(a, QualityAnalyzer):
                    raise
                session.notes.append(f"{a.name} analyzer failed: {type(e).__name__}: {e}"[:300])
                self._log(f"analyze[{a.name}] failed, continuing: {e}")
        return session

    def run(self, source: str, brief: Brief, workdir: str, out: str) -> tuple[Session, ReelPlan, RenderResult]:
        session = self.analyse(source, brief, workdir)
        if not session.candidates():
            raise ValueError("nothing usable after the quality filter")
        plan = self.selector.select(session)
        self._log(f"select[{plan.selector}]: {len(plan.items)} shots, {plan.seconds:.1f}s")
        result = self.renderer.render(plan, session, out)
        self._log(f"render: {result.path} {result.width}x{result.height} {result.seconds}s {result.size_mb} MB")
        report = {"result": to_dict(result), "plan": to_dict(plan), "events": self.events,
                  **session_dict(session)}
        Path(out).with_suffix(".json").write_text(json.dumps(report, indent=2))
        return session, plan, result


def build_pipeline(config: dict | None = None, llm: VisionLLM | None = None) -> Pipeline:
    cfg = {**DEFAULT_CONFIG, **(config or {})}
    needs_llm = "vision" in cfg["analyzers"] or cfg["selector"] in ("director", "timeline")
    if needs_llm and llm is None:
        llm = build_llm(cfg["llm"].get("provider"), cfg["llm"].get("model"))
    analyzers: list[Analyzer] = []
    for name in cfg["analyzers"]:
        if name == "quality":
            analyzers.append(QualityAnalyzer())
        elif name == "vision":
            analyzers.append(VisionAnalyzer(llm))
        else:
            raise ValueError(f"unknown analyzer '{name}'")
    selectors = {"heuristic": lambda: HeuristicSelector(), "director": lambda: DirectorSelector(llm),
                 "timeline": lambda: TimelineSelector(llm)}
    if cfg["selector"] not in selectors:
        raise ValueError(f"unknown selector '{cfg['selector']}'")
    if cfg["ingestor"] != "folder" or cfg["sampler"] != "ffmpeg_scene" or cfg["renderer"] != "ffmpeg":
        raise ValueError("only ingestor=folder, sampler=ffmpeg_scene, renderer=ffmpeg are implemented")
    return Pipeline(FolderIngestor(), FFmpegSceneSampler(), analyzers, selectors[cfg["selector"]](),
                    FFmpegRenderer())
