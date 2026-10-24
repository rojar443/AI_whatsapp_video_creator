"""Speech-to-text for voice notes."""
from __future__ import annotations

import json
from abc import ABC, abstractmethod
from dataclasses import dataclass

ALLOWED_AUDIO_MIME = {"audio/ogg", "audio/opus", "audio/mpeg", "audio/mp3", "audio/mp4", "audio/m4a", "audio/x-m4a",
                      "audio/aac", "audio/amr", "audio/wav", "audio/x-wav", "audio/webm"}
_MAGIC = [b"OggS", b"ID3", b"\xff\xfb", b"\xff\xf3", b"\xff\xf2", b"#!AMR", b"RIFF", b"\x1aE\xdf\xa3", b"\xff\xf1",
          b"\xff\xf9"]


def base_mime(mime: str | None) -> str:
    return (mime or "").split(";")[0].strip().lower()


def looks_like_audio(data: bytes, mime: str | None) -> bool:
    if base_mime(mime) not in ALLOWED_AUDIO_MIME:
        return False
    head = data[:16]
    return any(head.startswith(m) for m in _MAGIC) or (len(head) >= 8 and head[4:8] == b"ftyp")


@dataclass
class Transcript:
    text: str
    seconds: float | None = None


class TranscriptionError(Exception):
    pass


class TranscriptionProvider(ABC):
    name = "base"

    @abstractmethod
    def transcribe(self, audio: bytes, mime: str) -> Transcript: ...


class MockTranscriptionProvider(TranscriptionProvider):
    """Offline: reads a transcript hint appended by the dev simulator / test fixtures
    (`\\n#REELCHAT-TRANSCRIPT#{json}`). Audio without a hint fails — exercises that path."""

    name = "mock"
    MARKER = b"\n#REELCHAT-TRANSCRIPT#"

    def transcribe(self, audio: bytes, mime: str) -> Transcript:
        idx = audio.rfind(self.MARKER)
        if idx == -1:
            raise TranscriptionError("no speech detected (mock provider needs a transcript hint)")
        try:
            text = str(json.loads(audio[idx + len(self.MARKER):].decode()).get("text") or "").strip()
        except (ValueError, UnicodeDecodeError) as e:
            raise TranscriptionError("unreadable transcript hint") from e
        if not text:
            raise TranscriptionError("empty transcript")
        return Transcript(text=text)

    @classmethod
    def embed(cls, audio: bytes, text: str) -> bytes:
        return audio + cls.MARKER + json.dumps({"text": text}).encode()


class GeminiTranscriptionProvider(TranscriptionProvider):
    name = "gemini"

    def __init__(self, agent=None):
        from app.agents.llm import GeminiAgentProvider
        from app.config import get_settings

        s = get_settings()
        self.agent = agent or GeminiAgentProvider({"default": s.ai_model}, api_key=s.gemini_api_key)

    def transcribe(self, audio: bytes, mime: str) -> Transcript:
        from app.agents.base import AgentError

        try:
            text = self.agent.transcribe(audio, base_mime(mime) or "audio/ogg")
        except AgentError as e:
            raise TranscriptionError(str(e)) from e
        if not text:
            raise TranscriptionError("no speech detected")
        return Transcript(text=text)
