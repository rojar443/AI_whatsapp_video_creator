"""Text-to-speech providers. Each writes a WAV/MP3 file for one scene's narration."""
from __future__ import annotations

import shutil
import subprocess
from abc import ABC, abstractmethod

ESPEAK_VOICES = {"english": "en", "hindi": "hi", "malayalam": "ml", "tamil": "ta", "spanish": "es", "french": "fr"}


class TTSError(Exception):
    pass


class TTSProvider(ABC):
    name = "base"
    is_mock = False
    languages: list[str] = list(ESPEAK_VOICES)

    @abstractmethod
    def synthesize(self, text: str, language: str, voice: str, pace: str, out_path: str) -> str:
        """Write audio to out_path (any ffmpeg-readable format) and return the path."""


class MockTTSProvider(TTSProvider):
    """Writes silence sized to the text length. Clearly not speech — dev/test only."""

    name = "mock"
    is_mock = True

    def __init__(self, ffmpeg: str = "ffmpeg"):
        self.ffmpeg = ffmpeg

    def synthesize(self, text: str, language: str, voice: str, pace: str, out_path: str) -> str:
        wps = {"slow": 2.0, "normal": 2.6, "fast": 3.2}.get(pace, 2.6)
        seconds = max(1.0, len(text.split()) / wps)
        cmd = [self.ffmpeg, "-y", "-loglevel", "error", "-f", "lavfi", "-i",
               "anullsrc=r=44100:cl=mono", "-t", f"{seconds:.2f}", out_path]
        if subprocess.run(cmd, capture_output=True).returncode != 0:
            raise TTSError("mock tts failed")
        return out_path


class EspeakTTSProvider(TTSProvider):
    """Free offline TTS (robotic but real speech). Good for demos without API keys."""

    name = "espeak"

    def __init__(self, binary: str | None = None):
        self.binary = binary or shutil.which("espeak-ng") or shutil.which("espeak")
        if not self.binary:
            raise ValueError("espeak-ng is not installed")

    def synthesize(self, text: str, language: str, voice: str, pace: str, out_path: str) -> str:
        v = ESPEAK_VOICES.get(language, "en") + ("+m3" if voice == "male" else "+f3")
        speed = {"slow": "140", "normal": "165", "fast": "190"}.get(pace, "165")
        r = subprocess.run([self.binary, "-v", v, "-s", speed, "-w", out_path, "--", text],
                           capture_output=True, timeout=60)
        if r.returncode != 0:
            raise TTSError(f"espeak failed: {r.stderr.decode(errors='ignore')[:200]}")
        return out_path


class OpenAITTSProvider(TTSProvider):
    name = "openai"
    VOICES = {"female": "nova", "male": "onyx"}

    def __init__(self, api_key: str, model: str):
        from openai import OpenAI

        if not api_key:
            raise ValueError("OPENAI_API_KEY is required for TTS_PROVIDER=openai")
        self.client = OpenAI(api_key=api_key, timeout=60)
        self.model = model

    def synthesize(self, text: str, language: str, voice: str, pace: str, out_path: str) -> str:
        speed = {"slow": 0.9, "normal": 1.0, "fast": 1.15}.get(pace, 1.0)
        try:
            with self.client.audio.speech.with_streaming_response.create(
                model=self.model, voice=self.VOICES.get(voice, "nova"), input=text,
                response_format="wav", speed=speed,
            ) as resp:
                resp.stream_to_file(out_path)
        except Exception as e:  # noqa: BLE001
            raise TTSError(f"openai tts failed: {type(e).__name__}") from e
        return out_path


def get_tts(name: str | None = None) -> TTSProvider:
    from app.config import get_settings

    s = get_settings()
    name = name or s.tts_provider
    if name == "espeak":
        return EspeakTTSProvider()
    if name == "openai":
        return OpenAITTSProvider(s.openai_api_key or "", s.tts_model)
    return MockTTSProvider(s.ffmpeg_path)
