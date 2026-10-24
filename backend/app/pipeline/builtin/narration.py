"""Built-in narration: configured TTS provider (ElevenLabs by default) with word timings."""
from __future__ import annotations

from pathlib import Path

from app import providers
from app.media import ffmpeg
from app.pipeline.contracts import NarrationInput, NarrationOutput, WordTiming
from app.pipeline.registry import StepError
from app.tts import TTSError


class Narrator:
    def run(self, inp: NarrationInput) -> NarrationOutput:
        tts = providers.get_tts()
        out = str(Path(inp.work_dir) / ("narration.mp3" if tts.name == "elevenlabs" else "narration.wav"))
        try:
            speech = tts.synthesize(inp.text, out, vibe=inp.vibe)
        except TTSError as e:
            raise StepError(str(e), retryable=e.retryable, code="narration_failed") from e
        return NarrationOutput(audio_path=speech.path, duration=ffmpeg.duration(speech.path),
                               words=[WordTiming(word=w, start=s, end=e) for w, s, e in speech.words],
                               provider=tts.name, is_demo=tts.is_demo)
