"""VideoProvider interface. Shared steps (script, scenes, voiceover, captions, assembly,
validation) live here; providers mainly differ in how they produce visuals."""
from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.agents.base import Script
from app.tts import TTSProvider
from app.video import ffmpeg

log = logging.getLogger(__name__)

RESOLUTIONS = {"standard": (720, 1280), "high": (1080, 1920)}
SCENE_SECONDS = {"slow": 7.5, "normal": 6.0, "fast": 4.0}
WORDS_PER_SECOND = {"slow": 2.0, "normal": 2.4, "fast": 2.8}


class GenerationError(Exception):
    """retryable=True for timeouts/5xx from providers; False for bad input or validation."""

    def __init__(self, message: str, code: str = "generation_failed", retryable: bool = False):
        super().__init__(message)
        self.code = code
        self.retryable = retryable


@dataclass
class PlannedScene:
    index: int
    narration: str
    on_screen_text: str
    visual_query: str
    start: float
    seconds: float


@dataclass
class RenderResult:
    path: str
    width: int
    height: int
    seconds: float
    script: Script
    srt_path: str | None
    report: ffmpeg.OutputReport


class VideoProvider(ABC):
    name = "base"
    is_mock = False
    cost_per_second_usd = 0.0

    def __init__(self, tts: TTSProvider, script_writer=None):
        self.tts = tts
        self.script_writer = script_writer  # AgentProvider with write_script()

    # ------------------------------------------------------------------ capabilities
    def capabilities(self) -> dict[str, Any]:
        return {
            "qualities": ["standard", "high"],
            "languages": self.tts.languages,
            "styles": ["cinematic", "realistic", "animation", "documentary", "minimalist"],
            "music": ["none", "upbeat", "calm", "dramatic", "inspiring"],
            "max_duration": 60,
            "voice": True,
        }

    # ------------------------------------------------------------------ steps
    def plan(self, prefs: dict[str, Any]) -> tuple[int, int]:
        total = int(prefs.get("duration_seconds") or 30)
        pace = prefs.get("pace") or "normal"
        count = max(2, min(12, round(total / SCENE_SECONDS.get(pace, 6.0))))
        words = max(4, int(total / count * WORDS_PER_SECOND.get(pace, 2.4)))
        return count, words

    def generate_script(self, prefs: dict[str, Any]) -> Script:
        count, words = self.plan(prefs)
        if prefs.get("script_mode") == "user" and prefs.get("user_script"):
            return script_from_user_text(prefs["user_script"], prefs, count)
        from app.agents.rules import RulesAgentProvider

        writer = self.script_writer or RulesAgentProvider()
        try:
            script = writer.write_script(prefs, count, words)
        except Exception as e:  # noqa: BLE001 - LLM failure degrades to template script
            log.warning("script writer %s failed (%s); using template script", writer.name, type(e).__name__)
            script = RulesAgentProvider().write_script(prefs, count, words)
        if not script.scenes:
            raise GenerationError("script has no scenes", code="script_empty")
        return script

    def generate_scenes(self, script: Script, prefs: dict[str, Any]) -> list[PlannedScene]:
        total = float(prefs.get("duration_seconds") or 30)
        weights = [max(3, len(s.narration.split())) for s in script.scenes]
        unit = total / sum(weights)
        scenes, t = [], 0.0
        for i, (s, wgt) in enumerate(zip(script.scenes, weights, strict=True)):
            secs = round(wgt * unit, 3) if i < len(weights) - 1 else round(total - t, 3)
            scenes.append(PlannedScene(i, s.narration, s.on_screen_text, s.visual_query, round(t, 3), secs))
            t += secs
        return scenes

    @abstractmethod
    def generate_video(self, scenes: list[PlannedScene], prefs: dict[str, Any], workdir: str) -> list[str]:
        """Produce one silent clip per scene at the target resolution."""

    def add_voiceover(self, scenes: list[PlannedScene], prefs: dict[str, Any], workdir: str) -> str | None:
        if prefs.get("voice") == "none":
            return None
        parts = []
        for sc in scenes:
            raw = str(Path(workdir) / f"tts_{sc.index:02d}.wav")
            fitted = str(Path(workdir) / f"narr_{sc.index:02d}.wav")
            self.tts.synthesize(sc.narration, prefs.get("language") or "english",
                                prefs.get("voice") or "female", prefs.get("pace") or "normal", raw)
            ffmpeg.fit_audio(raw, fitted, sc.seconds)
            parts.append(fitted)
        return ffmpeg.concat(parts, str(Path(workdir) / "narration.wav"), workdir, audio=True)

    def add_captions(self, scenes: list[PlannedScene], prefs: dict[str, Any], workdir: str) -> str | None:
        if not prefs.get("captions_enabled", True):
            return None
        lines = []
        n = 1
        for sc in scenes:
            words = sc.narration.split()
            chunks = [" ".join(words[i:i + 6]) for i in range(0, len(words), 6)] or [sc.on_screen_text]
            step = sc.seconds / len(chunks)
            for j, chunk in enumerate(chunks):
                a = sc.start + j * step
                b = min(sc.start + (j + 1) * step, sc.start + sc.seconds) - 0.05
                lines.append(f"{n}\n{ffmpeg.srt_time(a)} --> {ffmpeg.srt_time(b)}\n{chunk}\n")
                n += 1
        path = Path(workdir) / "captions.srt"
        path.write_text("\n".join(lines), encoding="utf-8")
        return str(path)

    def assemble_video(self, clips: list[str], narration: str | None, captions: str | None,
                       prefs: dict[str, Any], workdir: str) -> str:
        w, h = self.resolution(prefs)
        total = float(prefs.get("duration_seconds") or 30)
        video = ffmpeg.concat(clips, str(Path(workdir) / "visuals.mp4"), workdir)
        music = None
        if (prefs.get("music") or "none") != "none":
            music = ffmpeg.synth_music(prefs["music"], total, str(Path(workdir) / "music.wav"))
        return ffmpeg.assemble(video, narration, music, captions, str(Path(workdir) / "final.mp4"),
                               workdir, total, h)

    def validate_output(self, path: str, prefs: dict[str, Any]) -> ffmpeg.OutputReport:
        w, h = self.resolution(prefs)
        return ffmpeg.validate_mp4(path, w, h, float(prefs.get("duration_seconds") or 30))

    def resolution(self, prefs: dict[str, Any]) -> tuple[int, int]:
        return RESOLUTIONS.get(prefs.get("quality") or "standard", RESOLUTIONS["standard"])

    def health(self) -> dict[str, Any]:
        return {"ok": ffmpeg.available(), "provider": self.name, "tts": self.tts.name,
                "mock": self.is_mock, "error": None if ffmpeg.available() else "ffmpeg not found"}

    # ------------------------------------------------------------------ orchestration
    def render(self, prefs: dict[str, Any], workdir: str, on_stage=lambda stage: None) -> RenderResult:
        on_stage("script")
        script = self.generate_script(prefs)
        scenes = self.generate_scenes(script, prefs)
        on_stage("visuals")
        clips = self.generate_video(scenes, prefs, workdir)
        on_stage("voiceover")
        narration = self.add_voiceover(scenes, prefs, workdir)
        captions = self.add_captions(scenes, prefs, workdir)
        on_stage("assembly")
        out = self.assemble_video(clips, narration, captions, prefs, workdir)
        on_stage("validation")
        report = self.validate_output(out, prefs)
        if not report.ok:
            raise GenerationError("output validation failed: " + "; ".join(report.problems),
                                  code="invalid_output")
        w, h = self.resolution(prefs)
        return RenderResult(out, w, h, report.duration, script, captions, report)


def script_from_user_text(text: str, prefs: dict[str, Any], count: int) -> Script:
    import re

    from app.agents.base import Scene

    sentences = [s.strip() for s in re.split(r"(?<=[.!?।])\s+|\n+", text) if s.strip()]
    if not sentences:
        raise GenerationError("your script is empty", code="script_empty")
    count = min(count, len(sentences))
    per = -(-len(sentences) // count)
    groups = [" ".join(sentences[i:i + per]) for i in range(0, len(sentences), per)]
    topic = prefs.get("topic") or "video"
    return Script(
        title=str(topic)[:80],
        scenes=[Scene(narration=g, on_screen_text=g[:60], visual_query=str(topic)) for g in groups],
    )
