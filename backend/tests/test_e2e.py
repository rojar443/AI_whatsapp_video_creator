"""End-to-end acceptance test: the full MVP journey through the public HTTP surface,
signed webhooks, the queue and real FFmpeg rendering with the mock providers."""
from datetime import timedelta

from app.config import get_settings
from app.jobs.tasks import run_pending
from app.models import GeneratedAsset, GenerationJob, Message, VideoProject, WhatsAppAccount, utcnow
from app.storage import get_storage
from app.transcription import MockTranscriptionProvider
from tests.conftest import (
    FIXTURES,
    confirm_button,
    fresh_client,
    link_whatsapp,
    outbound,
    register,
    requires_ffmpeg,
    say,
    wa_button,
    wa_voice,
)


@requires_ffmpeg
def test_full_journey_text_confirm_generate_deliver_revise(client, db):
    # 1-2. Register, verify, link WhatsApp
    register(client)
    wa = link_whatsapp(client, db)
    assert client.get("/api/me").json()["whatsapp"]["status"] == "linked"

    # 3-5. Text request + follow-up questions
    r = say(client, db, wa, "Create a motivational Reel about fitness")
    assert any("Where will you post it" in x for x in r), r  # vibe given, style inferred → asks format/length
    r = say(client, db, wa, "15 seconds, standard quality")
    summary = r[-1]
    assert "Here's your video plan" in summary
    assert "Topic: fitness" in summary and "Length: 15 s" in summary and "Style: Cinematic _(suggested)_" in summary

    # 6. Confirm (button with nonce)
    wa_button(client, wa, confirm_button(db, wa), "✅ Generate")
    run_pending(db)

    # 7-8. Progress notifications + delivery
    bodies = [m.body or "" for m in outbound(db, wa)]
    assert any("queued" in b for b in bodies)
    assert any("being generated" in b for b in bodies)
    assert any("mock preview" in b for b in bodies)  # mock output is labelled as such
    video_msg = [m for m in outbound(db, wa) if m.kind in ("video", "link")][-1]
    assert video_msg.status == "sent" and video_msg.payload["delivered_as"] == "video"
    asset = db.get(GeneratedAsset, video_msg.payload["asset_id"])
    assert asset.width == 720 and asset.height == 1280 and asset.is_mock
    assert get_storage().exists(asset.storage_key)
    anon = fresh_client()
    dl = anon.get(video_msg.payload["url"].replace("http://testserver", ""))
    assert dl.status_code == 200 and dl.content[4:8] == b"ftyp"

    # 9. Revision → new summary → confirm → second video
    r = say(client, db, wa, "Make it more cinematic and use a faster pace")
    assert any("revision 2" in x and "pace" in x for x in r), r
    r = say(client, db, wa, "yes")
    assert any("queued" in x for x in r)
    project = db.query(VideoProject).one()
    db.refresh(project)
    assert project.revision == 2 and project.status == "completed"
    assert db.query(GenerationJob).filter_by(status="completed").count() == 2

    # 10. History on the website
    items = client.get("/api/projects").json()["items"]
    assert len(items) == 1 and items[0]["status"] == "completed" and items[0]["video"]["url"]
    detail = client.get(f"/api/projects/{project.id}").json()
    assert [p["revision"] for p in detail["preferences"]] == [1, 2]
    assert detail["preferences"][1]["values"]["pace"] == "fast"
    assert detail["preferences"][0]["sources"]["topic"] == "user"
    assert len([a for a in detail["assets"] if a["kind"] == "video"]) == 2
    assert client.get("/api/me").json()["credits"] == 10 - 1 - 1

    # Resend link on request
    r = say(client, db, wa, "send the link again")
    assert any("fresh download link" in x for x in r)


@requires_ffmpeg
def test_voice_note_request_end_to_end(client, db):
    register(client)
    wa = link_whatsapp(client, db)
    audio = (FIXTURES / "voice_note.ogg").read_bytes()
    text = (FIXTURES / "voice_note.txt").read_text().strip()
    st = get_storage()
    st.put_bytes("dev-media/v1", MockTranscriptionProvider.embed(audio, text))
    st.put_bytes("dev-media/v1.mime", b"audio/ogg")
    wa_voice(client, wa, "v1")
    run_pending(db)
    assert "Here's your video plan" in outbound(db, wa)[-1].body
    r = say(client, db, wa, "yes")
    assert any("queued" in x for x in r)
    assert db.query(GenerationJob).one().status == "completed"


def test_large_video_falls_back_to_link_and_template_outside_window(client, db, fake_video, monkeypatch):
    register(client)
    wa = link_whatsapp(client, db)
    monkeypatch.setattr(get_settings(), "whatsapp_max_video_bytes", 10)
    say(client, db, wa, "make a funny 15 second youtube short about cats, cinematic, standard quality")
    say(client, db, wa, "yes")
    link_msg = [m for m in outbound(db, wa) if m.kind == "link"][-1]
    assert link_msg.payload["delivered_as"] == "link" and "/api/files/" in link_msg.payload["url"]

    # Outside the 24h customer-service window only templates may be sent.
    db.query(WhatsAppAccount).update({"last_inbound_at": utcnow() - timedelta(hours=30)})
    db.commit()
    from app.conversation.engine import ConversationEngine
    from app.models import Conversation
    from app.services.outbox import Outbox

    conv = db.query(Conversation).one()
    out = Outbox(db, conv)
    out.text("Your video is ready")
    out.flush()
    db.commit()
    run_pending(db)
    last = db.query(Message).filter_by(direction="outbound").order_by(Message.created_at.desc()).first()
    assert last.payload and last.payload.get("via_template") is True
    assert ConversationEngine  # imported for side-effect free check
