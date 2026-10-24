"""Text-to-speech providers. Each returns the audio file plus word timings when available."""
from __future__ import annotations

import base64
import shutil
import subprocess
from abc import ABC, abstractmethod
from dataclasses import dataclass, field

import httpx


class TTSError(Exception):
    def __init__(self, message: str, retryable: bool = False):
        super().__init__(message)
        self.retryable = retryable


@dataclass
class Speech:
    path: str
    words: list[tuple[str, float, float]] = field(default_factory=list)  # (word, start, end)


class TTSProvider(ABC):
    name = "base"
    is_demo = False

    @abstractmethod
    def synthesize(self, text: str, out_path: str, vibe: str | None = None, voice_id: str | None = None) -> Speech: ...


def words_from_alignment(alignment: dict) -> list[tuple[str, float, float]]:
    """ElevenLabs character alignment → word timings."""
    chars = alignment.get("characters") or []
    starts = alignment.get("character_start_times_seconds") or []
    ends = alignment.get("character_end_times_seconds") or []
    words, cur, w_start, w_end = [], "", None, 0.0
    for ch, s, e in zip(chars, starts, ends, strict=False):
        if ch.isspace():
            if cur:
                words.append((cur, w_start, w_end))
            cur, w_start = "", None
            continue
        if w_start is None:
            w_start = float(s)
        cur += ch
        w_end = float(e)
    if cur:
        words.append((cur, w_start or 0.0, w_end))
    return words


class ElevenLabsTTS(TTSProvider):
    name = "elevenlabs"

    def __init__(self, api_key: str, voice_id: str, model_id: str, base_url: str = "https://api.elevenlabs.io",
                 client: httpx.Client | None = None):
        if not api_key:
            raise ValueError("ELEVENLABS_API_KEY is required for TTS_PROVIDER=elevenlabs")
        self.voice_id, self.model_id, self.base = voice_id, model_id, base_url.rstrip("/")
        self.client = client or httpx.Client(timeout=120, headers={"xi-api-key": api_key})

    def synthesize(self, text, out_path, vibe=None, voice_id=None):
        calm = vibe in ("calm", "emotional", "cinematic")
        body = {"text": text, "model_id": self.model_id,
                "voice_settings": {"stability": 0.65 if calm else 0.45, "similarity_boost": 0.75}}
        url = f"{self.base}/v1/text-to-speech/{voice_id or self.voice_id}/with-timestamps"
        try:
            r = self.client.post(url, params={"output_format": "mp3_44100_128"}, json=body)
        except httpx.HTTPError as e:
            raise TTSError(f"elevenlabs unreachable: {e}", retryable=True) from e
        if r.status_code in (429,) or r.status_code >= 500:
            raise TTSError(f"elevenlabs error {r.status_code}", retryable=True)
        if r.status_code >= 400:
            raise TTSError(f"elevenlabs rejected the request ({r.status_code}): {r.text[:200]}")
        data = r.json()
        with open(out_path, "wb") as fh:
            fh.write(base64.b64decode(data["audio_base64"]))
        return Speech(out_path, words_from_alignment(data.get("alignment") or {}))

    def voices(self) -> list[dict]:
        r = self.client.get(f"{self.base}/v1/voices")
        r.raise_for_status()
        return [{"voice_id": v["voice_id"], "name": v.get("name")} for v in r.json().get("voices", [])]


class EspeakTTS(TTSProvider):
    """Free offline voice (robotic) — demo mode without an ElevenLabs key."""

    name = "espeak"
    is_demo = True

    def __init__(self):
        self.binary = shutil.which("espeak-ng") or shutil.which("espeak")
        if not self.binary:
            raise ValueError("espeak-ng is not installed")

    def synthesize(self, text, out_path, vibe=None, voice_id=None):
        r = subprocess.run([self.binary, "-v", "en-us+f3", "-s", "150", "-w", out_path, "--", text],
                           capture_output=True, timeout=60)
        if r.returncode != 0:
            raise TTSError(f"espeak failed: {r.stderr.decode(errors='ignore')[:200]}")
        return Speech(out_path)


class MockTTS(TTSProvider):
    """Silence sized to the text (tests)."""

    name = "mock"
    is_demo = True

    def synthesize(self, text, out_path, vibe=None, voice_id=None):
        from app.media import ffmpeg

        seconds = max(1.0, len(text.split()) / 2.5)
        ffmpeg.run(["-f", "lavfi", "-i", "anullsrc=r=44100:cl=mono", "-t", f"{seconds:.2f}", out_path])
        return Speech(out_path)


def get_tts(name: str | None = None) -> TTSProvider:
    from app.config import get_settings

    s = get_settings()
    name = name or s.tts_provider
    if name == "elevenlabs":
        return ElevenLabsTTS(s.elevenlabs_api_key or "", s.elevenlabs_voice_id, s.elevenlabs_model_id,
                             s.elevenlabs_base_url)
    if name == "espeak":
        return EspeakTTS()
    return MockTTS()
