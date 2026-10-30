"""Built-in moment scoring: the configured AI provider's vision model scores one frame per
segment against the brief + playbook. Without a vision model it falls back to a heuristic
(exposure, contrast, segment length, photo/video mix) so the pipeline still works offline."""
from __future__ import annotations

import logging
from pathlib import Path

from app import providers
from app.agents import diagnostics
from app.media import ffmpeg
from app.pipeline.builtin.quality import mean
from app.pipeline.contracts import ScoringInput, ScoringOutput, SegmentScore

log = logging.getLogger(__name__)


def heuristic_score(seg, frame_gray: bytes | None) -> SegmentScore:
    score = 5.0
    if frame_gray:
        m = mean(frame_gray)
        var = sum((p - m) ** 2 for p in frame_gray) / len(frame_gray)
        score += 2.0 - abs(m - 125) / 45  # well exposed
        score += min(2.0, (var ** 0.5) / 30)  # contrast / detail
    if seg.kind == "video":
        length = seg.end - seg.start
        score += 0.5 if 1.5 <= length <= 5 else 0
    return SegmentScore(segment_id=seg.segment_id, score=round(max(0.0, min(10.0, score)), 2),
                        reason="well exposed, good detail" if score >= 7 else "usable shot")


class MomentScorer:
    def run(self, inp: ScoringInput) -> ScoringOutput:
        agent = providers.get_agent()
        if agent.supports_vision:
            try:
                frames = {s.segment_id: [Path(p).read_bytes() for p in s.frame_paths[:1] if Path(p).exists()]
                          for s in inp.segments}
                scores = agent.score_moments(inp.segments, frames, inp.context)
                got = {s.segment_id for s in scores}
                missing = [s for s in inp.segments if s.segment_id not in got]
                if missing:
                    diagnostics.report("vision", agent, f"Vision returned no scores for {len(missing)} of "
                                       f"{len(inp.segments)} moments; those moments used heuristic scoring.",
                                       "partial_vision")
                scores += [self._heuristic(s) for s in missing]
                return ScoringOutput(scores=scores, scorer=f"{agent.name}-vision")
            except Exception as e:  # noqa: BLE001
                diagnostics.report("vision", agent, e)
                log.warning("vision scoring via %s failed (%s); using heuristic", agent.name, e)
        else:
            diagnostics.report("vision", agent, diagnostics.unavailable(agent), "ai_unavailable")
        return ScoringOutput(scores=[self._heuristic(s) for s in inp.segments], scorer="heuristic")

    def _heuristic(self, seg) -> SegmentScore:
        gray = None
        if seg.frame_paths and Path(seg.frame_paths[0]).exists():
            frames = ffmpeg.gray_frames(seg.frame_paths[0], [0.0])
            gray = frames[0] if frames else None
        return heuristic_score(seg, gray)
