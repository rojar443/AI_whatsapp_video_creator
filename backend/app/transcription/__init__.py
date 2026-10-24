"""Speech-to-text providers."""
from __future__ import annotations

import io
import json
from abc import ABC, abstractmethod
from dataclasses import dataclass

ALLOWED_AUDIO_MIME = {
    "audio/ogg", "audio/opus", "audio/mpeg", "audio/mp3", "audio/mp4", "audio/m4a",
    "audio/x-m4a", "audio/aac", "audio/amr", "audio/wav", "audio/x-wav", "audio/webm",
}
# Magic bytes for the containers above — the declared mime type alone isn't trusted.
_MAGIC = [b"OggS", b"ID3", b"\xff\xfb", b"\xff\xf3", b"\xff\xf2", b"#!AMR", b"RIFF", b"\x1aE\xdf\xa3", b"\xff\xf1", b"\xff\xf9"]


def base_mime(mime: str | None) -> str:
    return (mime or "").split(";")[0].strip().lower()


def looks_like_audio(data: bytes, mime: str | None) -> bool:
    if base_mime(mime) not in ALLOWED_AUDIO_MIME:
        return False
    head = data[:16]
    if any(head.startswith(m) for m in _MAGIC):
        return True
    return len(head) >= 8 and head[4:8] == b"ftyp"  # mp4/m4a


@dataclass
class Transcript:
    text: str
    language: str | None = None
    seconds: float | None = None


class TranscriptionError(Exception):
    pass


class TranscriptionProvider(ABC):
    name = "base"

    @abstractmethod
    def transcribe(self, audio: bytes, mime: str, language_hint: str | None = None) -> Transcript: ...


class MockTranscriptionProvider(TranscriptionProvider):
    """Deterministic, offline. Reads a transcript embedded by the dev simulator / test fixtures.

    The simulator stores voice notes as real audio followed by a trailer
    `\\n#REELCHAT-TRANSCRIPT#{json}` — the mock returns that text. Audio without a trailer
    fails, which exercises the 'couldn't understand' path.
    """

    name = "mock"
    MARKER = b"\n#REELCHAT-TRANSCRIPT#"

    def transcribe(self, audio: bytes, mime: str, language_hint: str | None = None) -> Transcript:
        idx = audio.rfind(self.MARKER)
        if idx == -1:
            raise TranscriptionError("no speech detected (mock provider needs a transcript hint)")
        try:
            info = json.loads(audio[idx + len(self.MARKER):].decode())
        except (ValueError, UnicodeDecodeError) as e:
            raise TranscriptionError("unreadable transcript hint") from e
        text = str(info.get("text") or "").strip()
        if not text:
            raise TranscriptionError("empty transcript")
        return Transcript(text=text, language=info.get("language"), seconds=info.get("seconds"))

    @classmethod
    def embed(cls, audio: bytes, text: str, language: str | None = None) -> bytes:
        return audio + cls.MARKER + json.dumps({"text": text, "language": language}).encode()


class OpenAITranscriptionProvider(TranscriptionProvider):
    name = "openai"

    def __init__(self, api_key: str, model: str):
        from openai import OpenAI

        if not api_key:
            raise ValueError("OPENAI_API_KEY is required for TRANSCRIPTION_PROVIDER=openai")
        self.client = OpenAI(api_key=api_key, timeout=60)
        self.model = model

    def transcribe(self, audio: bytes, mime: str, language_hint: str | None = None) -> Transcript:
        ext = {"audio/ogg": "ogg", "audio/mpeg": "mp3", "audio/mp4": "m4a", "audio/amr": "amr",
               "audio/wav": "wav", "audio/webm": "webm", "audio/aac": "aac"}.get(base_mime(mime), "ogg")
        buf = io.BytesIO(audio)
        buf.name = f"voice.{ext}"
        try:
            res = self.client.audio.transcriptions.create(model=self.model, file=buf)
        except Exception as e:  # noqa: BLE001 - surface any SDK error as a transcription failure
            raise TranscriptionError(f"transcription failed: {type(e).__name__}") from e
        text = (getattr(res, "text", "") or "").strip()
        if not text:
            raise TranscriptionError("empty transcript")
        return Transcript(text=text)


def get_transcriber(name: str | None = None) -> TranscriptionProvider:
    from app.config import get_settings

    s = get_settings()
    name = name or s.transcription_provider
    if name == "openai":
        return OpenAITranscriptionProvider(s.openai_api_key or "", s.transcription_model)
    return MockTranscriptionProvider()
