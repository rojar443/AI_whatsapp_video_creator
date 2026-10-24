"""Voice-note retrieval, validation and transcription using fixtures (no API calls)."""
import pytest

from app.jobs.tasks import run_pending
from app.models import Message, VideoProject
from app.storage import get_storage
from app.transcription import MockTranscriptionProvider, TranscriptionError, looks_like_audio
from tests.conftest import FIXTURES, last_reply, link_whatsapp, outbound, register, wa_voice

TRANSCRIPT = (FIXTURES / "voice_note.txt").read_text().strip()


def fixture_audio() -> bytes:
    return (FIXTURES / "voice_note.ogg").read_bytes()


def put_media(media_id: str, data: bytes, mime="audio/ogg") -> None:
    st = get_storage()
    st.put_bytes(f"dev-media/{media_id}", data)
    st.put_bytes(f"dev-media/{media_id}.mime", mime.encode())


def test_fixture_is_real_ogg():
    assert fixture_audio().startswith(b"OggS")
    assert looks_like_audio(fixture_audio(), "audio/ogg; codecs=opus")
    assert not looks_like_audio(b"<html>evil</html>", "audio/ogg")
    assert not looks_like_audio(fixture_audio(), "application/pdf")


def test_mock_transcriber_reads_embedded_hint():
    t = MockTranscriptionProvider()
    assert t.transcribe(MockTranscriptionProvider.embed(fixture_audio(), TRANSCRIPT), "audio/ogg").text == TRANSCRIPT
    with pytest.raises(TranscriptionError):
        t.transcribe(fixture_audio(), "audio/ogg")


def test_voice_note_drives_same_flow_as_text(client, db):
    register(client)
    wa = link_whatsapp(client, db)
    put_media("m1", MockTranscriptionProvider.embed(fixture_audio(), TRANSCRIPT))
    wa_voice(client, wa, "m1")
    run_pending(db)
    inbound = db.query(Message).filter_by(direction="inbound", kind="audio").one()
    assert inbound.transcript == TRANSCRIPT and inbound.status == "processed"
    project = db.query(VideoProject).one()
    assert project.title.lower().startswith("fitness")
    assert not get_storage().exists("dev-media/m1")  # recording discarded after transcription
    assert "Here's your video plan" in last_reply(db, wa).body or "vibe" in last_reply(db, wa).body


def test_unintelligible_voice_note(client, db):
    register(client)
    wa = link_whatsapp(client, db)
    put_media("m2", fixture_audio())  # no transcript hint → mock STT fails
    wa_voice(client, wa, "m2")
    run_pending(db)
    assert "couldn't understand" in last_reply(db, wa).body
    assert db.query(Message).filter_by(kind="audio").one().status == "failed"


def test_non_audio_and_oversized_media_rejected(client, db, monkeypatch):
    register(client)
    wa = link_whatsapp(client, db)
    put_media("m3", b"MZ\x90\x00 executable", mime="audio/ogg")
    wa_voice(client, wa, "m3")
    run_pending(db)
    assert "text and voice notes" in last_reply(db, wa).body
    from app.config import get_settings

    monkeypatch.setattr(get_settings(), "max_voice_note_bytes", 100)
    put_media("m4", MockTranscriptionProvider.embed(fixture_audio(), TRANSCRIPT))
    wa_voice(client, wa, "m4")
    run_pending(db)
    assert "too long" in last_reply(db, wa).body


def test_missing_media(client, db):
    register(client)
    wa = link_whatsapp(client, db)
    n = len(outbound(db, wa))
    wa_voice(client, wa, "does-not-exist")
    run_pending(db)
    assert len(outbound(db, wa)) == n + 1
    assert "couldn't understand" in last_reply(db, wa).body
