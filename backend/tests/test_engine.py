"""ConversationEngine branch coverage with a scripted agent: every intent, button, tool and guard."""
from datetime import timedelta

import pytest

from app import providers
from app.agents.base import AgentError, MessageAnalysis, UsageInfo
from app.agents.rules import RulesAgentProvider
from app.conversation import engine as E
from app.conversation.engine import ConversationEngine, link_code_hash, retry_failed_job
from app.jobs.dispatch import INLINE
from app.messaging.base import SendError
from app.models import (
    Conversation,
    GeneratedAsset,
    GenerationJob,
    Message,
    ProjectAsset,
    UsageRecord,
    User,
    VideoPreferences,
    VideoProject,
    WhatsAppAccount,
    utcnow,
)
from app.security import sign_payload
from app.services.outbox import Outbox
from app.storage import get_storage
from app.transcription import MockTranscriptionProvider, TranscriptionError

S = E.S


class Scripted(RulesAgentProvider):
    """Rules agent whose analysis can be forced per message."""

    def __init__(self):
        super().__init__()
        self.queue: list = []
        self.usage: list = []

    def analyze_message(self, text, ctx):
        self.last_context = ctx
        if self.queue:
            nxt = self.queue.pop(0)
            if isinstance(nxt, Exception):
                raise nxt
            self.last_usage = list(self.usage)
            return nxt
        return super().analyze_message(text, ctx)


def A(intent="other", **kw):
    return MessageAnalysis(intent=intent, **kw)


@pytest.fixture
def agent():
    a = Scripted()
    providers._overrides["agent"] = a
    return a


@pytest.fixture
def world(db):
    user = User(name="Asha Nair", email="asha@x.test", credits_balance=10)
    db.add(user)
    db.flush()
    wa = WhatsAppAccount(wa_id="15550001111", user_id=user.id, status="linked", notice_sent_at=utcnow(),
                         last_inbound_at=utcnow())
    db.add(wa)
    db.flush()
    conv = Conversation(whatsapp_account_id=wa.id, user_id=user.id)
    db.add(conv)
    db.commit()
    return user, wa, conv


def make_project(db, conv, user, status=S.collecting_media.value, media=0, plan=None, revision=1, **kw):
    p = VideoProject(user_id=user.id, conversation_id=conv.id, status=status, revision=revision, asked=[],
                     last_media_at=utcnow(), **kw)
    db.add(p)
    db.flush()
    for r in range(1, revision + 1):
        db.add(VideoPreferences(project_id=p.id, revision=r, field_sources={}, plan=plan if r == revision else None))
    for i in range(media):
        db.add(ProjectAsset(project_id=p.id, user_id=user.id, kind="video", media_id=f"m{i}", status="ready"))
    conv.active_project_id = p.id
    db.commit()
    return p


def send(db, conv, text="", kind="text", payload=None, media_id=None, analysis=None, agent=None):
    if analysis is not None:
        agent.queue.append(analysis)
    n = db.query(Message).filter_by(conversation_id=conv.id, direction="outbound").count()
    m = Message(conversation_id=conv.id, direction="inbound", kind=kind, body=text, payload=payload,
                media_id=media_id, status="received")
    db.add(m)
    db.commit()
    ConversationEngine(db).handle_inbound(m.id)
    db.expire_all()
    out = db.query(Message).filter_by(conversation_id=conv.id, direction="outbound").order_by(Message.created_at).all()
    return [x.body or "" for x in out[n:]], db.get(Message, m.id)


def run_tool(db, conv, fn, *args):
    out = Outbox(db, conv)
    res = fn(*args, out)
    out.flush()
    db.commit()
    texts = [m.body for m in db.query(Message).filter(Message.id.in_(
        [t[1][0] for t in INLINE.ready if t[0] == "deliver"][-1] if INLINE.ready else [])).all()]
    return res, " | ".join(texts)


def last_texts(db, conv, n=3):
    db.expire_all()
    rows = db.query(Message).filter_by(conversation_id=conv.id, direction="outbound") \
        .order_by(Message.created_at.desc()).limit(n).all()
    return " | ".join(m.body or "" for m in reversed(rows))


# ------------------------------------------------------------------ helpers
def test_link_code_hash_depends_on_wa_and_code():
    assert link_code_hash("1", "123456") != link_code_hash("2", "123456")
    assert link_code_hash("1", "123456") == link_code_hash("1", "123456")


def test_media_counts_and_summary(db, world):
    user, _, conv = world
    p = make_project(db, conv, user, media=2)
    db.add(ProjectAsset(project_id=p.id, user_id=user.id, kind="photo", media_id="ph", status="pending"))
    db.add(ProjectAsset(project_id=p.id, user_id=user.id, kind="photo", media_id="bad", status="rejected"))
    db.commit()
    assert E.media_counts(db, p.id) == (2, 1)
    assert "2" in E.summary_of(db, p.id)


# ------------------------------------------------------------------ handle_inbound guards
def test_handle_inbound_is_idempotent(db, world, agent):
    _, _, conv = world
    _, m = send(db, conv, "hi")
    assert m.status == "processed"
    n = db.query(Message).count()
    ConversationEngine(db).handle_inbound(m.id)
    ConversationEngine(db).handle_inbound("missing")
    assert db.query(Message).count() == n


def test_handle_inbound_ignores_outbound(db, world):
    _, _, conv = world
    m = Message(conversation_id=conv.id, direction="outbound", kind="text", status="received")
    db.add(m)
    db.commit()
    ConversationEngine(db).handle_inbound(m.id)
    assert db.get(Message, m.id).status == "received"


def test_handle_inbound_crash_replies_apology(db, world, agent, monkeypatch):
    _, _, conv = world
    monkeypatch.setattr(ConversationEngine, "_text", lambda *a, **k: 1 / 0)
    texts, m = send(db, conv, "hi")
    assert m.status == "failed" and m.error == "internal error"
    assert texts == ["Sorry, something went wrong on my side. Please try again in a moment."]
    assert db.get(Conversation, conv.id).last_message_at is not None


def test_rate_limit(db, world, agent, monkeypatch):
    from app.config import get_settings

    _, _, conv = world
    monkeypatch.setattr(get_settings(), "inbound_messages_per_minute", 2)
    for _ in range(2):
        send(db, conv, "hi", analysis=A("greeting"), agent=agent)
    texts, m = send(db, conv, "hi")
    assert m.status == "ignored" and texts == [E.replies.render("rate_limited")]
    texts, m = send(db, conv, "hi")
    assert m.status == "ignored" and texts == []  # warned only once


def test_media_not_rate_limited(db, world, agent, monkeypatch):
    from app.config import get_settings

    _, _, conv = world
    monkeypatch.setattr(get_settings(), "inbound_messages_per_minute", 0)
    _, m = send(db, conv, kind="video", media_id="v1", payload={"media_mime": "video/mp4"})
    assert m.status == "processed"


def test_anonymous_profile_created_when_missing(db, world, agent):
    user, wa, conv = world
    wa.user_id, conv.user_id = None, None
    db.commit()
    send(db, conv, "hi", analysis=A("greeting"), agent=agent)
    db.expire_all()
    wa = db.get(WhatsAppAccount, wa.id)
    anon = db.get(User, wa.user_id)
    assert anon.is_anonymous and db.get(Conversation, conv.id).user_id == anon.id


def test_suspended_user(db, world, agent):
    user, _, conv = world
    user.status = "suspended"
    db.commit()
    assert send(db, conv, "hi")[0] == [E.replies.render("suspended")]


def test_first_contact_notice_once(db, world, agent):
    _, wa, conv = world
    wa.notice_sent_at, wa.profile_name = None, "Asha Nair"
    db.commit()
    texts, _ = send(db, conv, "hi", analysis=A("greeting"), agent=agent)
    assert texts[0].startswith("Hi Asha!")
    texts, _ = send(db, conv, "hi", analysis=A("greeting"), agent=agent)
    assert not texts[0].startswith("Hi")


def test_unsupported_kind(db, world, agent):
    _, _, conv = world
    texts, m = send(db, conv, kind="sticker")
    assert texts == [E.replies.render("unsupported_media")] and m.status == "ignored"


def test_held_media_released(db, world, agent):
    _, _, conv = world
    held = Message(conversation_id=conv.id, direction="outbound", kind="video", status="held")
    db.add(held)
    db.commit()
    INLINE.clear()
    send(db, conv, "hi", analysis=A("greeting"), agent=agent)
    assert db.get(Message, held.id).status == "pending"
    assert any(t[0] == "deliver" and held.id in t[1][0] for t in INLINE.ready)


# ------------------------------------------------------------------ simple intents
@pytest.mark.parametrize("intent,key", [("help", "help"), ("off_topic", "off_topic")])
def test_static_intents(db, world, agent, intent, key):
    _, _, conv = world
    assert send(db, conv, "x", analysis=A(intent), agent=agent)[0] == [E.replies.render(key)]


def test_register_intent_includes_phone(db, world, agent):
    _, _, conv = world
    texts, _ = send(db, conv, "x", analysis=A("register"), agent=agent)
    assert "%2B15550001111" in texts[0]


def test_register_intent_telegram_has_no_phone(db, world, agent):
    _, wa, conv = world
    wa.wa_id = "tg:42"
    db.commit()
    texts, _ = send(db, conv, "x", analysis=A("register"), agent=agent)
    assert "phone=" in texts[0] and "%2B" not in texts[0]


def test_unsupported_notice(db, world, agent):
    _, _, conv = world
    texts, _ = send(db, conv, "x", analysis=A("greeting", unsupported=["4K", "Spanish"]), agent=agent)
    assert texts[0] == "Heads up: 4K; Spanish."
    texts, _ = send(db, conv, "x", analysis=A("help", unsupported=["4K"]), agent=agent)
    assert texts == [E.replies.render("help")]


def test_status(db, world, agent):
    user, _, conv = world
    assert send(db, conv, "x", analysis=A("status"), agent=agent)[0] == [E.replies.render("status_none")]
    p = make_project(db, conv, user, status=S.awaiting_preferences.value, stage="planning", title="Goa")
    assert send(db, conv, "x", analysis=A("status"), agent=agent)[0] == ["Your reel *Goa* is being planned."]
    p.stage = None
    db.commit()
    assert "Goa" in send(db, conv, "x", analysis=A("status"), agent=agent)[0][0]


def test_new_project_cancels_open(db, world, agent):
    user, _, conv = world
    p = make_project(db, conv, user, media=1)
    texts, _ = send(db, conv, "x", analysis=A("new_project"), agent=agent)
    assert texts == [E.replies.render("new_project")]
    db.expire_all()
    assert db.get(VideoProject, p.id).status == S.cancelled.value
    assert db.get(Conversation, conv.id).active_project_id is None


def test_new_project_keeps_completed(db, world, agent):
    user, _, conv = world
    p = make_project(db, conv, user, status=S.completed.value)
    send(db, conv, "x", analysis=A("new_project"), agent=agent)
    assert db.get(VideoProject, p.id).status == S.completed.value


def test_clarify_fallback(db, world, agent):
    _, _, conv = world
    texts, _ = send(db, conv, "x", analysis=A("other"), agent=agent)
    assert texts == [agent.generate_response("clarify", {})]


def test_greeting_without_project(db, world, agent):
    _, _, conv = world
    assert send(db, conv, "x", analysis=A("greeting"), agent=agent)[0] == [E.replies.render("status_none")]


# ------------------------------------------------------------------ agent fallback & usage
def test_agent_failure_falls_back_to_rules(db, world, agent):
    _, _, conv = world
    agent.queue.append(AgentError("down"))
    texts, m = send(db, conv, "help")
    assert m.status == "processed" and texts == [E.replies.render("help")]


def test_agent_usage_is_recorded(db, world, agent):
    user, _, conv = world
    agent.usage = [UsageInfo("gemini", "gemini-flash-latest", 100, 20)]
    send(db, conv, "x", analysis=A("help"), agent=agent)
    rec = db.query(UsageRecord).filter_by(kind="llm").one()
    assert rec.quantity == 120 and rec.user_id == user.id


def test_context_has_pending_question_and_recent(db, world, agent):
    user, _, conv = world
    p = make_project(db, conv, user, status=S.awaiting_preferences.value, media=1)
    p.asked = ["genre"]
    db.commit()
    send(db, conv, "travel please", analysis=A("provide_info"), agent=agent)
    ctx = agent.last_context
    assert ctx.pending == "genre" and ctx.state == S.awaiting_preferences.value
    assert ctx.recent[-1] == ("user", "travel please") and ctx.media_summary


# ------------------------------------------------------------------ new project from text
@pytest.mark.parametrize("intent,key", [("create_reel", "send_media_first"), ("done_uploading", "need_media"),
                                        ("provide_info", "send_media_first")])
def test_text_first_creates_project(db, world, agent, intent, key):
    user, _, conv = world
    texts, _ = send(db, conv, "x", analysis=A(intent, fields={"genre": "travel"}), agent=agent)
    assert texts[-1] == E.replies.render(key)
    p = db.get(VideoProject, db.get(Conversation, conv.id).active_project_id)
    assert db.query(VideoPreferences).filter_by(project_id=p.id).one().genre == "travel"


def test_anonymous_project_limit(db, world, agent, monkeypatch):
    from app.config import get_settings

    user, _, conv = world
    user.is_anonymous = True
    db.commit()
    monkeypatch.setattr(get_settings(), "anon_max_projects", 1)
    make_project(db, conv, user, status=S.completed.value)
    texts, _ = send(db, conv, "x", analysis=A("create_reel"), agent=agent)
    assert texts[-1].startswith("You've used your free drafts")
    # media also blocked
    texts, _ = send(db, conv, kind="video", media_id="v9")
    assert texts[-1].startswith("You've used your free drafts")
    assert db.query(ProjectAsset).count() == 0


def test_anonymous_project_expiry(db, world, agent):
    from app.config import get_settings

    user, _, conv = world
    user.is_anonymous = True
    db.commit()
    send(db, conv, "x", analysis=A("create_reel"), agent=agent)
    p = db.query(VideoProject).one()
    days = (E.aware(p.expires_at) - utcnow()).days
    assert days in (get_settings().anon_retention_days - 1, get_settings().anon_retention_days)


# ------------------------------------------------------------------ media intake
def test_media_reopens_stale_plan(db, world, agent):
    user, _, conv = world
    p = make_project(db, conv, user, status=S.awaiting_confirmation.value, media=1, confirmation_nonce="n1",
                     stage="x")
    send(db, conv, kind="video", media_id="new", payload={"media_mime": "video/mp4", "caption": None})
    db.expire_all()
    p = db.get(VideoProject, p.id)
    assert p.status == S.collecting_media.value and p.confirmation_nonce is None and p.stage is None


def test_media_starts_new_project_after_completed(db, world, agent):
    user, _, conv = world
    old = make_project(db, conv, user, status=S.completed.value)
    send(db, conv, kind="image", media_id="p1", payload={"media_mime": "image/jpeg"})
    db.expire_all()
    new_id = db.get(Conversation, conv.id).active_project_id
    assert new_id != old.id
    assert db.query(ProjectAsset).filter_by(project_id=new_id).one().kind == "photo"


def test_too_many_files(db, world, agent, monkeypatch):
    from app.config import get_settings

    user, _, conv = world
    monkeypatch.setattr(get_settings(), "max_files_per_project", 2)
    make_project(db, conv, user, media=2)
    texts, m = send(db, conv, kind="video", media_id="v3")
    assert m.status == "ignored" and "maximum of 2" in texts[0]
    texts, m = send(db, conv, kind="video", media_id="v4")
    assert m.status == "ignored" and "maximum" in texts[0]  # n == limit: still warned (count unchanged)


def test_too_many_files_silent_past_limit(db, world, agent, monkeypatch):
    from app.config import get_settings

    user, _, conv = world
    monkeypatch.setattr(get_settings(), "max_files_per_project", 1)
    make_project(db, conv, user, media=2)
    texts, m = send(db, conv, kind="video", media_id="v3")
    assert m.status == "ignored" and texts == []


def test_duplicate_media_ignored(db, world, agent):
    user, _, conv = world
    p = make_project(db, conv, user)
    send(db, conv, kind="video", media_id="same")
    send(db, conv, kind="video", media_id="same")
    assert db.query(ProjectAsset).filter_by(project_id=p.id).count() == 1


def test_media_caption_sets_prefs_quietly(db, world, agent):
    user, _, conv = world
    p = make_project(db, conv, user)
    texts, _ = send(db, conv, kind="video", media_id="c1", payload={"caption": "goa trip"},
                    analysis=A("provide_info", fields={"genre": "travel"}), agent=agent)
    assert texts == []
    assert db.query(VideoPreferences).filter_by(project_id=p.id).one().genre == "travel"


# ------------------------------------------------------------------ collecting / preferences
def test_greeting_while_collecting_acks(db, world, agent):
    user, _, conv = world
    make_project(db, conv, user, media=1)
    texts, _ = send(db, conv, "hi", analysis=A("greeting"), agent=agent)
    assert texts[0].startswith("Got ") and "tell me what you want" in texts[0]


@pytest.mark.parametrize("analysis,key", [
    (A("provide_info", fields={"vibe": "calm"}), "batch_ack_prefs"),
    (A("other"), "clarify"),
    (A("create_reel"), "batch_ack_prefs"),
])
def test_collect_while_collecting(db, world, agent, analysis, key):
    user, _, conv = world
    p = make_project(db, conv, user, media=1)
    texts, _ = send(db, conv, "x", analysis=analysis, agent=agent)
    expected = E.replies.render(key, {"summary": E.summary_of(db, p.id)})
    assert texts == [expected]


def test_done_without_media(db, world, agent):
    user, _, conv = world
    make_project(db, conv, user)
    texts, _ = send(db, conv, "that's all", analysis=A("done_uploading"), agent=agent)
    assert texts == [E.replies.render("need_media")]


def test_done_asks_genre_then_defaults(db, world, agent):
    user, _, conv = world
    p = make_project(db, conv, user, media=1)
    texts, _ = send(db, conv, "that's all", analysis=A("done_uploading"), agent=agent)
    db.expire_all()
    out = db.query(Message).filter_by(conversation_id=conv.id, kind="interactive").one()
    assert [b["id"] for b in out.payload["buttons"]] == [f"genre:{p.id}:travel", f"genre:{p.id}:daily_life"]
    # asked once: confirming without a genre uses the default
    INLINE.clear()
    send(db, conv, "go", analysis=A("confirm"), agent=agent)
    db.expire_all()
    row = db.query(VideoPreferences).filter_by(project_id=p.id).one()
    assert row.genre == "travel" and (row.field_sources or {}).get("genre") == "default"
    assert db.get(VideoProject, p.id).stage == "planning"
    assert any(t[0] == "plan_project" for t in INLINE.ready)


def test_awaiting_prefs_unhelpful_reply_reasks(db, world, agent):
    user, _, conv = world
    p = make_project(db, conv, user, status=S.awaiting_preferences.value, media=1)
    texts, _ = send(db, conv, "hmm", analysis=A("other"), agent=agent)
    assert texts == [E.replies.render("ask_genre", {"summary": E.summary_of(db, p.id)})]


def test_close_batch_ignores_non_collecting(db, world):
    user, _, conv = world
    p = make_project(db, conv, user, status=S.awaiting_preferences.value, media=1)
    out = Outbox(db, conv)
    ConversationEngine(db).close_batch(p, out)
    assert out.ids == []


# ------------------------------------------------------------------ awaiting confirmation
PLAN = {"items": [{"segment_id": "s"}], "total_seconds": 30, "playbook": "travel@1"}


def test_confirmation_text_starts_render(db, world, agent):
    user, _, conv = world
    p = make_project(db, conv, user, status=S.awaiting_confirmation.value, media=1, plan=PLAN, confirmation_nonce="n")
    texts, _ = send(db, conv, "yes", analysis=A("confirm"), agent=agent)
    assert texts == [E.replies.render("queued")]
    db.expire_all()
    assert db.get(VideoProject, p.id).status == S.queued.value
    assert db.get(User, user.id).credits_balance == 8


def test_confirmation_with_changes_replans(db, world, agent):
    user, _, conv = world
    p = make_project(db, conv, user, status=S.awaiting_confirmation.value, media=1, plan=PLAN, confirmation_nonce="n")
    texts, _ = send(db, conv, "make it 15 seconds", analysis=A("modify", fields={"duration": "15"}), agent=agent)
    assert texts == [E.replies.render("replanning")]
    assert db.get(VideoProject, p.id).stage == "planning"


def test_confirmation_unclear_hint(db, world, agent):
    user, _, conv = world
    make_project(db, conv, user, status=S.awaiting_confirmation.value, plan=PLAN, confirmation_nonce="n")
    assert send(db, conv, "hmm maybe", analysis=A("other"), agent=agent)[0] == [E.replies.render("confirm_hint")]


@pytest.mark.parametrize("status", [S.queued.value, S.processing.value])
@pytest.mark.parametrize("analysis,key", [(A("modify"), "busy_modify"), (A("other", fields={"vibe": "calm"}), "busy_modify"),
                                          (A("other"), "already_processing")])
def test_running_project_replies(db, world, agent, status, analysis, key):
    user, _, conv = world
    make_project(db, conv, user, status=status)
    assert send(db, conv, "x", analysis=analysis, agent=agent)[0] == [E.replies.render(key)]


# ------------------------------------------------------------------ completed: revise / approve
def test_revise_completed(db, world, agent):
    user, _, conv = world
    p = make_project(db, conv, user, status=S.completed.value, media=1, plan=PLAN)
    texts, _ = send(db, conv, "shorter", analysis=A("modify", fields={"duration": "15"}), agent=agent)
    assert texts[0].startswith("Got it — revision 2") and texts[1] == E.replies.render("replanning")
    db.expire_all()
    p = db.get(VideoProject, p.id)
    assert p.revision == 2 and p.status == S.awaiting_preferences.value
    assert db.query(VideoPreferences).filter_by(project_id=p.id, revision=2).one().duration == "15"


def test_revise_without_changes(db, world, agent):
    user, _, conv = world
    make_project(db, conv, user, status=S.failed.value)
    assert send(db, conv, "change it", analysis=A("modify"), agent=agent)[0] == [E.replies.render("no_changes_detected")]


@pytest.mark.parametrize("anon,n", [(False, 1), (True, 2)])
def test_approve(db, world, agent, anon, n):
    user, _, conv = world
    user.is_anonymous = anon
    make_project(db, conv, user, status=S.completed.value)
    texts, _ = send(db, conv, "love it", analysis=A("approve"), agent=agent)
    assert texts[0] == E.replies.render("approve") and len(texts) == n


def test_project_of_other_user_is_not_active(db, world, agent):
    user, _, conv = world
    other = User(name="O")
    db.add(other)
    db.flush()
    make_project(db, conv, other, status=S.awaiting_confirmation.value)
    assert send(db, conv, "x", analysis=A("status"), agent=agent)[0] == [E.replies.render("status_none")]


# ------------------------------------------------------------------ buttons
def btn(db, conv, bid):
    return send(db, conv, "", kind="interactive", payload={"button_id": bid})[0]


def test_button_for_other_users_project(db, world, agent):
    user, _, conv = world
    other = User(name="O")
    db.add(other)
    db.flush()
    p = VideoProject(user_id=other.id)
    db.add(p)
    db.commit()
    assert btn(db, conv, f"cancel:{p.id}") == ["That button has expired. Send a new message to continue."]
    assert btn(db, conv, "cancel:missing") == ["That button has expired. Send a new message to continue."]


def test_retry_button_unknown_job(db, world, agent):
    _, _, conv = world
    assert btn(db, conv, "retry:nope") == [E.replies.render("nothing_to_retry")]


def test_retry_button_reruns_failed_job(db, world, agent):
    user, _, conv = world
    p = make_project(db, conv, user, status=S.failed.value, plan=PLAN)
    j = GenerationJob(project_id=p.id, user_id=user.id, revision=1, idempotency_key=f"{p.id}:r1", status="failed")
    db.add(j)
    db.commit()
    assert btn(db, conv, f"retry:{j.id}") == [E.replies.render("retry_started")]
    db.expire_all()
    assert db.get(GenerationJob, j.id).status == "queued" and db.get(GenerationJob, j.id).run_number == 2


def test_edit_button(db, world, agent):
    user, _, conv = world
    p = make_project(db, conv, user, status=S.awaiting_confirmation.value)
    assert btn(db, conv, f"edit:{p.id}")[0].startswith("Sure — what should I change?")


def test_cancel_button(db, world, agent):
    user, _, conv = world
    p = make_project(db, conv, user, status=S.awaiting_confirmation.value)
    assert btn(db, conv, f"cancel:{p.id}") == [E.replies.render("cancelled")]


def test_genre_button(db, world, agent):
    user, _, conv = world
    p = make_project(db, conv, user, status=S.awaiting_preferences.value, media=1)
    btn(db, conv, f"genre:{p.id}:daily_life")
    db.expire_all()
    assert db.query(VideoPreferences).filter_by(project_id=p.id).one().genre == "daily_life"
    assert db.get(VideoProject, p.id).stage == "planning"


def test_genre_button_wrong_state_falls_through_to_text(db, world, agent):
    user, _, conv = world
    p = make_project(db, conv, user, status=S.completed.value)
    agent.queue.append(A("help"))
    assert btn(db, conv, f"genre:{p.id}:travel") == [E.replies.render("help")]


def test_confirm_button_wrong_nonce(db, world, agent):
    user, _, conv = world
    p = make_project(db, conv, user, status=S.awaiting_confirmation.value, plan=PLAN, confirmation_nonce="good")
    assert btn(db, conv, f"confirm:{p.id}:bad") == [E.replies.render("plan_stale")]
    assert btn(db, conv, f"confirm:{p.id}") == [E.replies.render("plan_stale")]


# ------------------------------------------------------------------ tool_start_render
def engine_out(db, conv):
    return ConversationEngine(db), Outbox(db, conv)


def texts_of(db, out):
    db.flush()
    return [db.get(Message, i).body for i in out.ids]


def test_start_render_existing_job_states(db, world):
    user, _, conv = world
    p = make_project(db, conv, user, status=S.awaiting_confirmation.value, plan=PLAN, confirmation_nonce="n")
    j = GenerationJob(project_id=p.id, user_id=user.id, revision=1, idempotency_key=f"{p.id}:r1", status="queued")
    db.add(j)
    db.commit()
    for st, expected in [("queued", E.replies.render("already_processing")),
                         ("completed", "That version is already done ✅ Reply *link* for the download link."),
                         ("failed", "That version failed — reply *retry*.")]:
        j.status = st
        db.commit()
        eng, out = engine_out(db, conv)
        assert eng.tool_start_render(user, p, "n", out) is None
        assert texts_of(db, out) == [expected]


def test_start_render_not_awaiting(db, world):
    user, _, conv = world
    p = make_project(db, conv, user, status=S.completed.value)
    eng, out = engine_out(db, conv)
    eng.tool_start_render(user, p, "n", out)
    assert texts_of(db, out) == [E.replies.render("nothing_to_confirm")]


def test_start_render_without_plan_replans(db, world):
    user, _, conv = world
    p = make_project(db, conv, user, status=S.awaiting_confirmation.value, plan={"items": []}, confirmation_nonce="n")
    eng, out = engine_out(db, conv)
    assert eng.tool_start_render(user, p, "n", out) is None
    assert texts_of(db, out) == [E.replies.render("replanning")]


def test_start_render_daily_limit(db, world, monkeypatch):
    from app.config import get_settings

    user, _, conv = world
    monkeypatch.setattr(get_settings(), "max_jobs_per_day", 1)
    other = make_project(db, conv, user, status=S.completed.value)
    db.add(GenerationJob(project_id=other.id, user_id=user.id, revision=1, idempotency_key="x", status="completed"))
    p = make_project(db, conv, user, status=S.awaiting_confirmation.value, plan=PLAN, confirmation_nonce="n")
    eng, out = engine_out(db, conv)
    assert eng.tool_start_render(user, p, "n", out) is None
    assert texts_of(db, out) == [E.replies.render("daily_limit", {"limit": 1})]


def test_start_render_one_at_a_time(db, world):
    user, _, conv = world
    other = make_project(db, conv, user, status=S.processing.value)
    db.add(GenerationJob(project_id=other.id, user_id=user.id, revision=1, idempotency_key="x", status="processing"))
    p = make_project(db, conv, user, status=S.awaiting_confirmation.value, plan=PLAN, confirmation_nonce="n")
    eng, out = engine_out(db, conv)
    eng.tool_start_render(user, p, "n", out)
    assert "already have a reel in progress" in texts_of(db, out)[0]


@pytest.mark.parametrize("anon,prefix", [(False, "This reel needs 2 credits but you have 1"),
                                         (True, "You've used your free drafts")])
def test_start_render_insufficient_credits(db, world, anon, prefix):
    user, _, conv = world
    user.credits_balance, user.is_anonymous = 1, anon
    p = make_project(db, conv, user, status=S.awaiting_confirmation.value, plan=PLAN, confirmation_nonce="n")
    eng, out = engine_out(db, conv)
    assert eng.tool_start_render(user, p, "n", out) is None
    assert texts_of(db, out)[0].startswith(prefix)
    assert db.query(GenerationJob).count() == 0


def test_start_render_integrity_error(db, world, monkeypatch):
    from sqlalchemy.exc import IntegrityError

    user, _, conv = world
    p = make_project(db, conv, user, status=S.awaiting_confirmation.value, plan=PLAN, confirmation_nonce="n")

    def boom(*a):
        raise IntegrityError("x", {}, Exception())

    monkeypatch.setattr(E.credits, "reserve", boom)
    eng, out = engine_out(db, conv)
    assert eng.tool_start_render(user, p, "n", out) is None
    assert texts_of(db, out) == [E.replies.render("already_processing")]


# ------------------------------------------------------------------ tool_cancel
def test_cancel_nothing(db, world, agent):
    user, _, conv = world
    assert send(db, conv, "cancel", analysis=A("cancel"), agent=agent)[0] == [E.replies.render("nothing_to_cancel")]
    make_project(db, conv, user, status=S.completed.value)
    assert send(db, conv, "cancel", analysis=A("cancel"), agent=agent)[0] == [E.replies.render("nothing_to_cancel")]


def test_cancel_revision_keeps_previous(db, world, agent):
    user, _, conv = world
    p = make_project(db, conv, user, status=S.awaiting_confirmation.value, revision=2, plan=PLAN)
    db.add(GenerationJob(project_id=p.id, user_id=user.id, revision=1, idempotency_key=f"{p.id}:r1", status="completed"))
    db.commit()
    assert send(db, conv, "cancel", analysis=A("cancel"), agent=agent)[0] == [E.replies.render("kept_previous")]
    db.expire_all()
    p = db.get(VideoProject, p.id)
    assert p.revision == 1 and p.status == S.completed.value
    assert db.query(VideoPreferences).filter_by(project_id=p.id).count() == 1


def test_cancel_queued_refunds(db, world, agent):
    user, _, conv = world
    p = make_project(db, conv, user, status=S.queued.value)
    db.add(GenerationJob(project_id=p.id, user_id=user.id, revision=1, idempotency_key="k", status="queued",
                         credits_charged=3))
    db.commit()
    texts, _ = send(db, conv, "cancel", analysis=A("cancel"), agent=agent)
    assert texts == [E.replies.render("cancelled_refund", {"credits": 3})]
    db.expire_all()
    assert db.get(User, user.id).credits_balance == 13 and db.get(VideoProject, p.id).status == S.cancelled.value


def test_cancel_queued_without_job(db, world, agent):
    user, _, conv = world
    make_project(db, conv, user, status=S.queued.value)
    assert send(db, conv, "cancel", analysis=A("cancel"), agent=agent)[0] == [E.replies.render("nothing_to_cancel")]


def test_cancel_processing(db, world, agent):
    user, _, conv = world
    make_project(db, conv, user, status=S.processing.value)
    texts, _ = send(db, conv, "cancel", analysis=A("cancel"), agent=agent)
    assert texts == [E.replies.render("cannot_cancel_processing")]


# ------------------------------------------------------------------ tool_retry / retry_failed_job
def test_retry_nothing(db, world, agent):
    _, _, conv = world
    assert send(db, conv, "retry", analysis=A("retry"), agent=agent)[0] == [E.replies.render("nothing_to_retry")]


def test_retry_plan_failed_replans(db, world, agent):
    user, _, conv = world
    make_project(db, conv, user, status=S.awaiting_preferences.value, stage="plan_failed", media=1)
    assert send(db, conv, "retry", analysis=A("retry"), agent=agent)[0] == [E.replies.render("replanning")]


def failed_job(db, p, user, **kw):
    j = GenerationJob(project_id=p.id, user_id=user.id, revision=kw.pop("revision", p.revision),
                      idempotency_key=kw.pop("key", f"{p.id}:r{p.revision}"), status=kw.pop("status", "failed"), **kw)
    db.add(j)
    db.commit()
    return j


@pytest.mark.parametrize("status,key", [("queued", "already_processing"), ("processing", "already_processing"),
                                        ("completed", "nothing_to_retry")])
def test_retry_job_states(db, world, agent, status, key):
    user, _, conv = world
    p = make_project(db, conv, user, status=S.failed.value)
    failed_job(db, p, user, status=status)
    assert send(db, conv, "retry", analysis=A("retry"), agent=agent)[0] == [E.replies.render(key)]


def test_retry_old_revision(db, world):
    user, _, conv = world
    p = make_project(db, conv, user, status=S.failed.value, revision=2)
    failed_job(db, p, user, revision=1)
    assert retry_failed_job(db, p, user.id) == "nothing_to_retry"


def test_retry_no_jobs(db, world):
    user, _, conv = world
    p = make_project(db, conv, user, status=S.failed.value)
    assert retry_failed_job(db, p, user.id) == "nothing_to_retry"


def test_retry_insufficient_credits(db, world, agent):
    user, _, conv = world
    user.credits_balance = 0
    p = make_project(db, conv, user, status=S.failed.value, plan=PLAN)
    j = failed_job(db, p, user)
    texts, _ = send(db, conv, "retry", analysis=A("retry"), agent=agent)
    assert texts[0].startswith("This reel needs ? credits but you have 0")
    db.expire_all()
    j = db.get(GenerationJob, j.id)
    assert j.status == "failed" and j.run_number == 1


def test_retry_without_prefs_row_uses_default_cost(db, world):
    user, _, conv = world
    p = make_project(db, conv, user, status=S.failed.value)
    db.query(VideoPreferences).delete()
    j = failed_job(db, p, user)
    res = retry_failed_job(db, p, user.id)
    assert res is j and j.credits_charged == 2


# ------------------------------------------------------------------ resend link
def test_resend_link(db, world, agent):
    user, _, conv = world
    assert send(db, conv, "link", analysis=A("resend_link"), agent=agent)[0] == [E.replies.render("no_video_yet")]
    p = make_project(db, conv, user, status=S.completed.value)
    db.add(GeneratedAsset(user_id=user.id, project_id=p.id, kind="video", storage_key="k", mime_type="video/mp4"))
    db.commit()
    texts, _ = send(db, conv, "link", analysis=A("resend_link"), agent=agent)
    assert texts[0].startswith("Here's a fresh download link") and "/api/" in texts[0]


def test_resend_link_scoped_to_active_project(db, world, agent):
    user, _, conv = world
    old = make_project(db, conv, user, status=S.completed.value)
    db.add(GeneratedAsset(user_id=user.id, project_id=old.id, kind="video", storage_key="k", mime_type="video/mp4"))
    make_project(db, conv, user, status=S.completed.value)
    assert send(db, conv, "link", analysis=A("resend_link"), agent=agent)[0] == [E.replies.render("no_video_yet")]


# ------------------------------------------------------------------ WhatsApp LINK codes
@pytest.fixture
def anon_wa(db, world):
    """An anonymous WhatsApp number with a pending link to the registered user."""
    user, _, _ = world
    anon = User(name="", is_anonymous=True)
    db.add(anon)
    db.flush()
    wa = WhatsAppAccount(wa_id="15559990000", user_id=anon.id, status="pending", pending_user_id=user.id,
                         link_code_hash=link_code_hash("15559990000", "123456"),
                         link_code_expires_at=utcnow() + timedelta(minutes=10), notice_sent_at=utcnow())
    db.add(wa)
    db.flush()
    conv = Conversation(whatsapp_account_id=wa.id, user_id=anon.id)
    db.add(conv)
    db.commit()
    return user, anon, wa, conv


def test_link_success_unlinks_previous_number(db, world, anon_wa):
    user, old_wa, _ = world
    _, anon, wa, conv = anon_wa
    texts, _ = send(db, conv, "LINK 123456")
    assert texts[0].startswith("✅ Linked to Asha")
    db.expire_all()
    assert db.get(WhatsAppAccount, wa.id).user_id == user.id
    old = db.get(WhatsAppAccount, old_wa.id)
    assert old.user_id is None and old.status == "anonymous"


def test_link_wrong_code_counts_attempts(db, anon_wa):
    _, _, wa, conv = anon_wa
    texts, _ = send(db, conv, "LINK 000000")
    assert texts[0].startswith("This code didn't match")
    assert db.get(WhatsAppAccount, wa.id).link_attempts == 1


def test_link_already_linked(db, world):
    _, _, conv = world
    assert send(db, conv, "LINK 123456")[0] == [E.replies.render("already_linked")]


@pytest.mark.parametrize("change", ["suspended", "anonymous"])
def test_link_target_user_invalid(db, anon_wa, change):
    user, _, _, conv = anon_wa
    if change == "suspended":
        user.status = "suspended"
    else:
        user.is_anonymous = True
    db.commit()
    texts, _ = send(db, conv, "LINK 123456")
    assert texts[0].startswith("This code didn't match")


def test_present_plan_sets_nonce_and_buttons(db, world, monkeypatch):
    user, _, conv = world
    monkeypatch.setattr(E, "format_plan", lambda plan, prefs, cost, balance: f"plan cost={cost} bal={balance}")
    p = make_project(db, conv, user, status=S.awaiting_preferences.value, stage="planning", plan=PLAN)
    eng, out = engine_out(db, conv)
    eng.present_plan(p, out)
    assert p.status == S.awaiting_confirmation.value and p.stage is None and 8 <= len(p.confirmation_nonce) <= 12
    msg = db.get(Message, out.ids[0])
    assert msg.body == "plan cost=2 bal=10"
    assert [b["id"] for b in msg.payload["buttons"]] == [
        f"confirm:{p.id}:{p.confirmation_nonce}", f"edit:{p.id}", f"cancel:{p.id}"]


# ------------------------------------------------------------------ Telegram /start login_ & link_
@pytest.fixture
def tg(db):
    wa = WhatsAppAccount(wa_id="tg:777", status="anonymous", notice_sent_at=utcnow())
    db.add(wa)
    db.flush()
    conv = Conversation(whatsapp_account_id=wa.id)
    db.add(conv)
    db.commit()
    return wa, conv


def test_telegram_login_invalid_token(db, tg):
    _, conv = tg
    assert send(db, conv, "/start login_nope")[0][0].startswith("That login link didn't work")


def test_telegram_login_on_whatsapp_rejected(db, world):
    from app.services import login

    _, _, conv = world
    _, token, _ = login.create_token(db)
    db.commit()
    assert send(db, conv, f"/start login_{token}")[0][0].startswith("That login link didn't work")


def test_telegram_login_suspended(db, tg, monkeypatch):
    from app.models import LoginToken
    from app.services import accounts, login

    wa, conv = tg
    row, token, _ = login.create_token(db)
    db.commit()
    bad = User(name="B", status="suspended")
    db.add(bad)
    db.commit()
    monkeypatch.setattr(accounts, "resolve_telegram_login_user", lambda db_, w, c: bad)
    assert send(db, conv, f"/start login_{token}")[0] == [E.replies.render("suspended")]
    assert db.get(LoginToken, row.id).status == "denied"


def test_telegram_link_on_whatsapp_rejected(db, world):
    _, _, conv = world
    assert send(db, conv, "/start link_abc")[0][0].startswith("This code didn't match")


def test_telegram_link_bad_token(db, tg):
    _, conv = tg
    assert send(db, conv, "/start link_garbage")[0][0].startswith("This code didn't match")


def test_telegram_link_unknown_user(db, tg):
    _, conv = tg
    tok = sign_payload({"uid": "missing"}, "telegram-link", 600)
    assert send(db, conv, f"/start link_{tok}")[0][0].startswith("This code didn't match")


def test_telegram_link_success_and_already_linked(db, world, tg):
    user, _, _ = world
    wa, conv = tg
    other_tg = WhatsAppAccount(wa_id="tg:111", user_id=user.id, status="linked")
    db.add(other_tg)
    db.commit()
    tok = sign_payload({"uid": user.id}, "telegram-link", 600)
    texts, _ = send(db, conv, f"/start link_{tok}")
    assert texts[0].startswith("✅ Linked to Asha")
    db.expire_all()
    assert db.get(WhatsAppAccount, wa.id).user_id == user.id
    assert db.get(WhatsAppAccount, other_tg.id).user_id is None  # one Telegram chat per account
    assert db.get(WhatsAppAccount, world[1].id).user_id == user.id  # WhatsApp number untouched
    texts, _ = send(db, conv, f"/start link_{tok}")
    assert texts == [E.replies.render("already_linked")]


def test_telegram_link_merges_anonymous(db, world, tg):
    user, _, _ = world
    wa, conv = tg
    anon = User(name="", is_anonymous=True)
    db.add(anon)
    db.flush()
    wa.user_id = anon.id
    p = VideoProject(user_id=anon.id, conversation_id=conv.id)
    db.add(p)
    db.commit()
    tok = sign_payload({"uid": user.id}, "telegram-link", 600)
    send(db, conv, f"/start link_{tok}")
    db.expire_all()
    assert db.get(VideoProject, p.id).user_id == user.id


# ------------------------------------------------------------------ voice notes
class FakeDL:
    def __init__(self, data=b"", mime="audio/ogg", exc=None):
        self.data, self.mime, self.exc = data, mime, exc

    def download_media(self, media_id, dest, limit):
        if self.exc:
            raise self.exc
        open(dest, "wb").write(self.data)
        return self.mime


def voice(db, conv, **kw):
    return send(db, conv, kind="audio", media_id="vn1", payload={"media_mime": "audio/ogg"}, **kw)


def test_voice_download_failure(db, world, agent):
    _, _, conv = world
    providers._overrides["messaging"] = FakeDL(exc=SendError("gone"))
    texts, m = voice(db, conv)
    assert texts == [E.replies.render("voice_failed")] and m.status == "failed" and m.error == "gone"


def test_voice_not_audio(db, world, agent):
    _, _, conv = world
    providers._overrides["messaging"] = FakeDL(data=b"<html>")
    texts, m = voice(db, conv)
    assert texts == [E.replies.render("unsupported_media")] and m.error == "not an audio file"


def test_voice_transcription_failure(db, world, agent):
    _, _, conv = world
    providers._overrides["messaging"] = FakeDL(data=b"OggS" + b"\x00" * 10)
    texts, m = voice(db, conv)
    assert texts == [E.replies.render("voice_failed")] and "no speech" in m.error


def test_voice_success_records_transcript_and_cost(db, world, agent):
    user, _, conv = world
    st = get_storage()
    st.put_bytes("dev-media/vn1", b"x")
    st.put_bytes("dev-media/vn1.mime", b"audio/ogg")
    data = MockTranscriptionProvider.embed(b"OggS", "help me")
    providers._overrides["messaging"] = FakeDL(data=data, mime="")
    texts, m = voice(db, conv, analysis=A("help"), agent=agent)
    assert m.transcript == "help me" and texts == [E.replies.render("help")]
    assert not st.exists("dev-media/vn1") and not st.exists("dev-media/vn1.mime")
    assert db.query(UsageRecord).filter_by(kind="transcription").one().quantity == len(data)


def test_discard_dev_media_tolerates_errors(monkeypatch):
    from app import storage

    class Boom:
        def exists(self, k):
            raise OSError("x")

    monkeypatch.setattr(storage, "get_storage", lambda: Boom())
    ConversationEngine._discard_dev_media("abc")
    ConversationEngine._discard_dev_media(None)


def test_voice_custom_transcriber_error(db, world, agent):
    _, _, conv = world

    class T:
        name = "t"

        def transcribe(self, data, mime):
            raise TranscriptionError("garbled")

    providers._overrides["transcriber"] = T()
    providers._overrides["messaging"] = FakeDL(data=b"OggS")
    texts, m = voice(db, conv)
    assert m.error == "garbled"


# ------------------------------------------------------------------ prefs rows
def test_prefs_row_copies_previous_revision(db, world):
    user, _, conv = world
    p = make_project(db, conv, user)
    row = db.query(VideoPreferences).one()
    row.genre, row.vibe = "travel", "calm"
    p.revision = 3
    db.commit()
    new = ConversationEngine(db)._prefs_row(p)
    assert new.revision == 3 and new.genre == "travel" and new.vibe == "calm"


def test_prefs_row_created_when_none(db, world):
    user, _, conv = world
    p = VideoProject(user_id=user.id, conversation_id=conv.id)
    db.add(p)
    db.commit()
    assert ConversationEngine(db)._prefs_row(p).revision == 1
