"""Conversation state machine for the reel editor.

  collecting_media ──"that's all" / timeout──▶ awaiting_preferences ──genre known──▶ (planning)
        ▲  media                                   │ asks only the genre                │ plan ready
        │                                          ▼                                    ▼
        └──────── new media invalidates plan ── awaiting_confirmation ◀──────── plan message + buttons
                                                   │ explicit yes / button+nonce
                                                   ▼
                     cancelled ◀─cancel─ queued ──▶ processing ──▶ completed ──modify──▶ revision n+1
                                                               └─▶ failed ──retry──▶ queued (same job)

The agent only classifies messages and proposes field values. Every side effect is a
`tool_*` method here that re-checks ownership, state, quotas, credits and idempotency.
"""
from __future__ import annotations

import logging
from datetime import timedelta
from urllib.parse import quote

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app import providers
from app.agents.base import AgentContext, AgentProvider, MessageAnalysis
from app.agents.rules import RulesAgentProvider
from app.config import get_settings
from app.conversation import preferences as P
from app.conversation import replies
from app.conversation.plan_text import format_plan
from app.conversation.safety import is_explicit_confirmation, parse_link_code
from app.jobs.dispatch import enqueue
from app.models import (
    Conversation,
    GeneratedAsset,
    GenerationJob,
    JobStatus,
    Message,
    ProjectAsset,
    ProjectStatus,
    User,
    VideoPreferences,
    VideoProject,
    WhatsAppAccount,
    aware,
    utcnow,
)
from app.security import new_token, sha256_hex, sign_asset_url
from app.services import accounts, credits
from app.services.audit import audit
from app.services.outbox import Outbox
from app.transcription import TranscriptionError, looks_like_audio

log = logging.getLogger(__name__)
S = ProjectStatus
OPEN = (S.collecting_media.value, S.awaiting_preferences.value, S.awaiting_confirmation.value)
RUNNING = (S.queued.value, S.processing.value)
TERMINAL = (S.completed.value, S.failed.value, S.cancelled.value)


def link_code_hash(wa_id: str, code: str) -> str:
    return sha256_hex(f"{wa_id}:{code}:{get_settings().secret_key}")


def media_counts(db: Session, project_id: str) -> tuple[int, int]:
    rows = db.execute(select(ProjectAsset.kind, func.count(ProjectAsset.id)).where(
        ProjectAsset.project_id == project_id, ProjectAsset.status.in_(["pending", "ready"]))
        .group_by(ProjectAsset.kind)).all()
    d = dict(rows)
    return d.get("video", 0), d.get("photo", 0)


def summary_of(db: Session, project_id: str) -> str:
    return replies.media_summary(*media_counts(db, project_id))


class ConversationEngine:
    def __init__(self, db: Session, agent: AgentProvider | None = None):
        self.db = db
        self.s = get_settings()
        self.agent = agent or providers.get_agent()
        self.fallback = RulesAgentProvider()

    # ================================================================== entry point
    def handle_inbound(self, message_id: str) -> None:
        # FOR NO KEY UPDATE (key_share=True): serialises workers but doesn't block the API's inserts, whose
        # foreign-key checks take FOR KEY SHARE on the conversation — a plain FOR UPDATE deadlocked with them.
        msg = self.db.scalar(select(Message).where(Message.id == message_id).with_for_update(key_share=True))
        if msg is None or msg.direction != "inbound" or msg.status != "received":
            self.db.rollback()
            return  # already handled (idempotent)
        # Serialise per conversation so a burst of clips can't race into two projects.
        conv = self.db.scalar(select(Conversation).where(Conversation.id == msg.conversation_id)
                              .with_for_update(key_share=True))
        out = Outbox(self.db, conv)
        try:
            with self.db.begin_nested():
                self._handle(msg, conv, out)
            if msg.status == "received":
                msg.status = "processed"
        except Exception:  # noqa: BLE001 - never leave the user without a reply
            log.exception("failed to handle inbound message %s", message_id)
            out = Outbox(self.db, conv)
            msg.status, msg.error = "failed", "internal error"
            out.text("Sorry, something went wrong on my side. Please try again in a moment.")
        out.flush()
        conv.last_message_at = utcnow()
        self.db.commit()

    # ================================================================== dispatch
    def _handle(self, msg: Message, conv: Conversation, out: Outbox) -> None:
        wa: WhatsAppAccount = conv.whatsapp_account
        if self._rate_limited(conv, msg, out):
            return
        code = parse_link_code(msg.body or "") if msg.kind == "text" else None
        if code:
            self._handle_link(code, wa, conv, msg, out)
            return
        if msg.kind == "text" and (msg.body or "").startswith("/start login_"):
            self._handle_telegram_login(msg.body.split("login_", 1)[1].strip(), wa, conv, msg, out)
            return
        if msg.kind == "text" and (msg.body or "").startswith("/start link_"):
            self._handle_telegram_link(msg.body.split("link_", 1)[1].strip(), wa, conv, msg, out)
            return
        user = self.db.get(User, wa.user_id) if wa.user_id else None
        if user is None:
            user = accounts.create_anonymous(self.db, wa)
            conv.user_id = user.id
        msg.user_id = user.id
        accounts.apply_admin_ids(wa, user)
        if user.status != "active":
            out.text(replies.render("suspended"))
            return
        if wa.notice_sent_at is None:
            wa.notice_sent_at = utcnow()
            out.text(replies.render("first_contact", {"name": (wa.profile_name or "").split(" ")[0],
                                                      "web_url": self.s.web_base_url}))
        self._release_held_media(conv)

        if msg.kind in ("image", "video"):
            self._add_media(msg, user, conv, out)
            caption = (msg.payload or {}).get("caption")
            if caption:
                self._text(caption, msg, user, conv, out, from_caption=True)
            return
        if msg.kind == "audio":
            text = self._transcribe(msg, out)
            if text is None:
                return
        elif msg.kind in ("text", "interactive"):
            text = P.clean_text(msg.body or "", self.s.max_inbound_text_chars) or ""
        else:
            out.text(replies.render("unsupported_media"))
            msg.status = "ignored"
            return
        if msg.kind == "interactive" and (msg.payload or {}).get("button_id"):
            if self._handle_button(msg.payload["button_id"], user, conv, out):
                return
        self._text(text, msg, user, conv, out)

    def _text(self, text: str, msg: Message, user: User, conv: Conversation, out: Outbox,
              from_caption: bool = False) -> None:
        project = self._active(conv, user)
        out.project_id = project.id if project else None
        a = self._analyze(text, self._context(project, conv), user)
        state = project.status if project else None
        if a.unsupported and a.intent not in ("help", "status", "cancel"):
            out.text(replies.render("unsupported", {"items": "; ".join(a.unsupported)}))

        intent = a.intent
        if from_caption:  # captions only carry preferences / "that's all"
            if project and state in OPEN:
                self._collect(project, a, out, quiet=True)
            return
        if intent == "help":
            out.text(replies.render("help"))
            return
        if intent == "status":
            return self._status(project, out)
        if intent == "cancel":
            return self.tool_cancel(user, conv, project, out)
        if intent == "resend_link":
            return self.tool_resend_link(user, project, out)
        if intent == "retry":
            return self.tool_retry(user, project, out)
        if intent == "register":
            out.text(replies.render("register", {"web_url": self.s.web_base_url, "phone": self._phone_q(conv)}))
            return
        if intent == "off_topic":
            out.text(replies.render("off_topic"))
            return
        if intent == "new_project":
            if project and state in OPEN:
                project.status = S.cancelled.value
            conv.active_project_id = None
            out.text(replies.render("new_project"))
            return

        if state == S.awaiting_confirmation.value:
            if is_explicit_confirmation(text):
                self.tool_start_render(user, project, project.confirmation_nonce, out)
                return
            prefs = self._prefs(project)
            changed = P.apply_user_values(prefs, a.fields)
            if changed:
                self._save_prefs(project, prefs)
                self._start_planning(project, out, replan=True)
            else:
                out.text(replies.render("confirm_hint"))
            return
        if state in (S.collecting_media.value, S.awaiting_preferences.value):
            if intent == "greeting" and not a.fields:
                out.text(replies.render("batch_ack", {"summary": summary_of(self.db, project.id)}))
                return
            self._collect(project, a, out)
            return
        if state in RUNNING:
            out.text(replies.render("busy_modify" if (a.fields or intent == "modify") else "already_processing"))
            return

        # No open project.
        if project and state in (S.completed.value, S.failed.value) and (intent == "modify" or (
                a.fields and intent not in ("create_reel",))):
            self._revise(project, a, out)
            return
        if intent == "approve" and project and state == S.completed.value:
            out.text(replies.render("approve"))
            if user.is_anonymous:
                out.text(replies.render("anon_register_nudge", {"web_url": self.s.web_base_url,
                                                                "phone": self._phone_q(conv)}))
            return
        if intent in ("create_reel", "provide_info", "done_uploading") or a.fields:
            p = self._new_project(user, conv, out)
            if p is not None:
                prefs = self._prefs(p)
                P.apply_user_values(prefs, a.fields)
                self._save_prefs(p, prefs)
                out.text(replies.render("send_media_first" if intent != "done_uploading" else "need_media"))
            return
        if intent == "greeting":
            out.text(replies.render("status_none"))
            return
        out.text(self.agent.generate_response("clarify", {}))

    # ================================================================== media intake
    def _add_media(self, msg: Message, user: User, conv: Conversation, out: Outbox) -> None:
        project = self._active(conv, user)
        if project is None or project.status not in OPEN:
            project = self._new_project(user, conv, out)
            if project is None:
                return
        out.project_id = project.id
        n = self.db.scalar(select(func.count(ProjectAsset.id)).where(
            ProjectAsset.project_id == project.id, ProjectAsset.status.in_(["pending", "ready"])))
        if n >= self.s.max_files_per_project:
            if n == self.s.max_files_per_project:
                out.text(replies.render("too_many_files", {"limit": self.s.max_files_per_project}))
            msg.status = "ignored"
            return
        if project.status == S.awaiting_confirmation.value:  # plan is stale now
            project.confirmation_nonce = None
        project.status = S.collecting_media.value
        project.stage = None  # any in-flight plan is now stale
        project.batch_closed_at = None
        project.last_media_at = utcnow()
        asset = ProjectAsset(project_id=project.id, user_id=user.id, source_message_id=msg.id,
                             media_id=msg.media_id, kind="video" if msg.kind == "video" else "photo",
                             order_index=n, status="pending", mime_type=(msg.payload or {}).get("media_mime"),
                             caption=P.clean_text((msg.payload or {}).get("caption"), 500))
        try:
            with self.db.begin_nested():
                self.db.add(asset)
                self.db.flush()
        except IntegrityError:
            return  # same media twice
        enqueue(self.db, "download_asset", asset.id)
        enqueue(self.db, "batch_ack", project.id, n + 1, countdown=self.s.batch_ack_delay_seconds)
        enqueue(self.db, "batch_timeout", project.id, project.last_media_at.isoformat(),
                countdown=self.s.batch_timeout_seconds)

    def close_batch(self, project: VideoProject, out: Outbox) -> None:
        """Called on "that's all" or by the batch timeout."""
        if project.status != S.collecting_media.value:
            return
        videos, photos = media_counts(self.db, project.id)
        if videos + photos == 0:
            out.text(replies.render("need_media"))
            return
        project.batch_closed_at = utcnow()
        project.status = S.awaiting_preferences.value
        self._advance(project, out)

    def _collect(self, project: VideoProject, a: MessageAnalysis, out: Outbox, quiet: bool = False) -> None:
        prefs = self._prefs(project)
        changed = P.apply_user_values(prefs, a.fields)
        self._save_prefs(project, prefs)
        if project.status == S.collecting_media.value:
            if a.done:
                self.close_batch(project, out)
            elif changed and not quiet:
                out.text(replies.render("batch_ack_prefs", {"summary": summary_of(self.db, project.id)}))
            elif not quiet and a.intent not in ("create_reel", "provide_info"):
                out.text(replies.render("clarify"))
            elif not quiet:
                out.text(replies.render("batch_ack_prefs", {"summary": summary_of(self.db, project.id)}))
            return
        if project.status == S.awaiting_preferences.value:
            if not changed and not a.done and a.intent not in ("provide_info", "confirm"):
                if not quiet:
                    out.text(replies.render("ask_genre", {"summary": summary_of(self.db, project.id)}))
                return
            self._advance(project, out)

    def _advance(self, project: VideoProject, out: Outbox) -> None:
        prefs = self._prefs(project)
        asked = list(project.asked or [])
        if P.missing_essentials(prefs) and "genre" not in asked:
            asked.append("genre")
            project.asked = asked
            out.buttons(replies.render("ask_genre", {"summary": summary_of(self.db, project.id)}),
                        [(f"genre:{project.id}:travel", "✈️ Travel"), (f"genre:{project.id}:daily_life", "☀️ Daily life")])
            return
        if not prefs.get("genre"):  # asked once already; use the default
            prefs.values["genre"], prefs.sources["genre"] = "travel", "default"
        P.apply_defaults(prefs)
        self._save_prefs(project, prefs)
        self._start_planning(project, out)

    def _start_planning(self, project: VideoProject, out: Outbox, replan: bool = False) -> None:
        project.status = S.awaiting_preferences.value
        project.confirmation_nonce = None
        project.stage = "planning"
        row = self._prefs_row(project)
        row.plan = None
        enqueue(self.db, "plan_project", project.id, project.revision)
        out.text(replies.render("replanning" if replan else "planning",
                                {"summary": summary_of(self.db, project.id)}))

    def present_plan(self, project: VideoProject, out: Outbox) -> None:
        """Called by the planning task once the plan is stored."""
        row = self._prefs_row(project)
        prefs = P.Prefs.from_row(row)
        user = self.db.get(User, project.user_id)
        cost = P.credits_for(row.plan.get("total_seconds"), prefs.get("quality"))
        project.status = S.awaiting_confirmation.value
        project.stage = None
        project.confirmation_nonce = new_token(8)[:12]
        out.buttons(format_plan(row.plan, prefs, cost, user.credits_balance), [
            (f"confirm:{project.id}:{project.confirmation_nonce}", "✅ Proceed"),
            (f"edit:{project.id}", "✏️ Change"),
            (f"cancel:{project.id}", "✖ Cancel"),
        ])

    def _revise(self, project: VideoProject, a: MessageAnalysis, out: Outbox) -> None:
        current = self._prefs(project)
        before = current.copy()
        changed = P.apply_user_values(current, a.fields)
        if not changed:
            out.text(replies.render("no_changes_detected"))
            return
        project.revision += 1
        self._save_prefs(project, current)
        out.text(replies.render("revision_intro", {"revision": project.revision,
                                                   "changes": P.describe_changes(before, current, changed)}))
        self.db.get(Conversation, project.conversation_id).active_project_id = project.id
        self._start_planning(project, out, replan=True)

    # ================================================================== helpers
    def _active(self, conv: Conversation, user: User) -> VideoProject | None:
        p = self.db.get(VideoProject, conv.active_project_id) if conv.active_project_id else None
        return p if p is not None and p.user_id == user.id else None

    def _new_project(self, user: User, conv: Conversation, out: Outbox) -> VideoProject | None:
        if user.is_anonymous:
            n = self.db.scalar(select(func.count(VideoProject.id)).where(VideoProject.user_id == user.id))
            if n >= self.s.anon_max_projects:
                out.text(replies.render("anon_limit", {"web_url": self.s.web_base_url, "phone": self._phone_q(conv)}))
                return None
        days = self.s.anon_retention_days if user.is_anonymous else self.s.asset_retention_days
        project = VideoProject(user_id=user.id, conversation_id=conv.id, status=S.collecting_media.value, asked=[],
                               expires_at=utcnow() + timedelta(days=days))
        self.db.add(project)
        self.db.flush()
        self.db.add(VideoPreferences(project_id=project.id, revision=1, field_sources={}))
        conv.active_project_id = project.id
        out.project_id = project.id
        return project

    def _phone_q(self, conv: Conversation) -> str:
        wa_id = conv.whatsapp_account.wa_id
        return "" if wa_id.startswith("tg:") else quote("+" + wa_id)  # Telegram chats have no phone number

    def _rate_limited(self, conv: Conversation, msg: Message, out: Outbox) -> bool:
        if msg.kind in ("image", "video"):
            return False  # media bursts are expected; capped per project instead
        since = utcnow() - timedelta(seconds=60)
        n = self.db.scalar(select(func.count(Message.id)).where(
            Message.conversation_id == conv.id, Message.direction == "inbound", Message.created_at >= since,
            Message.kind.notin_(["image", "video"])))
        limit = self.s.inbound_messages_per_minute
        if n > limit:
            msg.status = "ignored"
            if n == limit + 1:
                out.text(replies.render("rate_limited"))
            return True
        return False

    def _handle_link(self, code: str, wa: WhatsAppAccount, conv: Conversation, msg: Message, out: Outbox) -> None:
        web = self.s.web_base_url
        current = self.db.get(User, wa.user_id) if wa.user_id else None
        if current is not None and not current.is_anonymous and wa.status == "linked":
            out.text(replies.render("already_linked"))
            return
        exp = aware(wa.link_code_expires_at)
        ok = (wa.pending_user_id and wa.link_code_hash and exp and exp > utcnow() and wa.link_attempts < 5
              and wa.link_code_hash == link_code_hash(wa.wa_id, code))
        if not ok:
            wa.link_attempts += 1
            out.text(replies.render("not_registered_link", {"web_url": web}))
            return
        user = self.db.get(User, wa.pending_user_id)
        if user is None or user.status != "active" or user.is_anonymous:
            out.text(replies.render("not_registered_link", {"web_url": web}))
            return
        for other in self.db.scalars(select(WhatsAppAccount).where(
                WhatsAppAccount.user_id == user.id, WhatsAppAccount.id != wa.id,
                WhatsAppAccount.wa_id.not_like("tg:%"))).all():  # one WhatsApp number per account
            other.user_id, other.status = None, "anonymous"
        wa.user_id = user.id
        wa.status = "linked"
        wa.linked_at = utcnow()
        wa.pending_user_id = wa.link_code_hash = wa.link_code_expires_at = None
        wa.link_attempts = 0
        self.db.flush()
        if current is not None and current.is_anonymous:
            accounts.merge_anonymous(self.db, current, user)
        conv.user_id = user.id
        msg.user_id = user.id
        out.conv.user_id = user.id
        audit(self.db, "whatsapp.linked", actor_id=user.id, target_type="whatsapp_account", target_id=wa.id)
        out.text(replies.render("linked", {"name": user.name.split(" ")[0], "web_url": web}))

    def _handle_telegram_login(self, token: str, wa: WhatsAppAccount, conv: Conversation, msg: Message,
                               out: Outbox) -> None:
        """`/start login_<token>` from the website's "Log in with Telegram" link or QR code."""
        from app.services import login

        web = self.s.web_base_url
        row = login.find_open(self.db, token) if wa.wa_id.startswith("tg:") else None
        if row is None:
            out.text(replies.render("login_failed", {"web_url": web}))
            return
        user = accounts.resolve_telegram_login_user(self.db, wa, conv)
        out.conv.user_id = msg.user_id = user.id
        if user.status != "active":
            row.status = "denied"
            out.text(replies.render("suspended"))
            return
        accounts.promote_anonymous(self.db, user, wa)
        row.status, row.user_id = "confirmed", user.id
        audit(self.db, "auth.telegram_login_confirmed", actor_id=user.id, target_type="login_token", target_id=row.id)
        out.text(replies.render("login_confirmed"))

    def _handle_telegram_link(self, token: str, wa: WhatsAppAccount, conv: Conversation, msg: Message,
                              out: Outbox) -> None:
        """`/start link_<signed token>` from the website's "Connect Telegram" button."""
        from app.security import TokenError, verify_payload

        web = self.s.web_base_url
        if not wa.wa_id.startswith("tg:"):
            out.text(replies.render("not_registered_link", {"web_url": web}))
            return
        try:
            data = verify_payload(token, "telegram-link")
        except TokenError:
            out.text(replies.render("not_registered_link", {"web_url": web}))
            return
        user = self.db.get(User, data.get("uid"))
        if user is None or user.status != "active" or user.is_anonymous:
            out.text(replies.render("not_registered_link", {"web_url": web}))
            return
        current = self.db.get(User, wa.user_id) if wa.user_id else None
        if current is not None and current.id == user.id:
            out.text(replies.render("already_linked"))
            return
        for other in self.db.scalars(select(WhatsAppAccount).where(
                WhatsAppAccount.user_id == user.id, WhatsAppAccount.id != wa.id,
                WhatsAppAccount.wa_id.like("tg:%"))).all():  # one Telegram chat per account
            other.user_id, other.status = None, "anonymous"
        wa.user_id, wa.status, wa.linked_at = user.id, "linked", utcnow()
        self.db.flush()
        if current is not None and current.is_anonymous:
            accounts.merge_anonymous(self.db, current, user)
        conv.user_id = user.id
        msg.user_id = user.id
        out.conv.user_id = user.id
        audit(self.db, "telegram.linked", actor_id=user.id, target_type="whatsapp_account", target_id=wa.id)
        out.text(replies.render("linked", {"name": user.name.split(" ")[0], "web_url": web}))

    def _release_held_media(self, conv: Conversation) -> None:
        """A reel finished outside the 24 h window was held; the user's reply reopens the window."""
        held = self.db.scalars(select(Message.id).where(Message.conversation_id == conv.id,
                                                        Message.status == "held")).all()
        if held:
            self.db.query(Message).filter(Message.id.in_(held)).update({"status": "pending"},
                                                                        synchronize_session=False)
            enqueue(self.db, "deliver", list(held))

    def _transcribe(self, msg: Message, out: Outbox) -> str | None:
        import tempfile
        from pathlib import Path

        from app.messaging.base import SendError

        with tempfile.TemporaryDirectory(dir=self._tmp()) as tmp:
            dest = str(Path(tmp) / "voice")
            try:
                mime = providers.get_messaging().download_media(msg.media_id or "", dest, self.s.max_voice_note_bytes)
            except SendError as e:
                out.text(replies.render("voice_failed"))
                msg.status, msg.error = "failed", str(e)[:200]
                return None
            data = Path(dest).read_bytes()
            mime = mime or (msg.payload or {}).get("media_mime") or "audio/ogg"
            if not looks_like_audio(data, mime):
                out.text(replies.render("unsupported_media"))
                msg.status, msg.error = "failed", "not an audio file"
                return None
            transcriber = providers.get_transcriber()
            try:
                result = transcriber.transcribe(data, mime)
            except TranscriptionError as e:
                out.text(replies.render("voice_failed"))
                msg.status, msg.error = "failed", str(e)[:200]
                return None
        # the recording is deleted with the temp dir; only the transcript is kept
        self._discard_dev_media(msg.media_id)
        credits.record_cost(self.db, user_id=msg.user_id, kind="transcription", provider=transcriber.name,
                            quantity=len(data), unit="bytes", cost_usd=0.0)
        text = P.clean_text(result.text, self.s.max_inbound_text_chars) or ""
        msg.transcript = text
        return text

    def _tmp(self) -> str:
        from pathlib import Path

        p = Path(self.s.work_dir) / "tmp"
        p.mkdir(parents=True, exist_ok=True)
        return str(p)

    @staticmethod
    def _discard_dev_media(media_id: str | None) -> None:
        if not media_id:
            return
        from app.storage import get_storage

        st = get_storage()
        for key in (f"dev-media/{media_id}", f"dev-media/{media_id}.mime"):
            try:
                if st.exists(key):
                    st.delete(key)
            except Exception:  # noqa: BLE001
                pass

    def _analyze(self, text: str, ctx: AgentContext, user: User) -> MessageAnalysis:
        try:
            self.agent.last_usage = []
            a = self.agent.analyze_message(text, ctx)
        except Exception as e:  # noqa: BLE001 - provider outage → deterministic fallback
            log.warning("agent %s failed (%s); falling back to rules", self.agent.name, type(e).__name__)
            a = self.fallback.analyze_message(text, ctx)
        for u in self.agent.last_usage:
            credits.record_cost(self.db, user_id=user.id, kind="llm", provider=u.provider,
                                quantity=u.input_tokens + u.output_tokens, unit="tokens",
                                cost_usd=credits.llm_cost(u.model, u.input_tokens, u.output_tokens))
        return a.sanitized()

    def _context(self, project: VideoProject | None, conv: Conversation) -> AgentContext:
        prefs = self._prefs(project) if project else P.Prefs()
        pending = None
        if project and project.status == S.awaiting_preferences.value and project.asked:
            pending = project.asked[-1]
        rows = self.db.scalars(select(Message).where(Message.conversation_id == conv.id)
                               .order_by(Message.created_at.desc()).limit(6)).all()
        recent = [("user" if m.direction == "inbound" else "assistant", (m.transcript or m.body or f"[{m.kind}]")[:300])
                  for m in reversed(rows)]
        return AgentContext(state=project.status if project else "none", pending=pending,
                            prefs=dict(prefs.values),
                            media_summary=summary_of(self.db, project.id) if project else "",
                            recent=recent)

    def _prefs_row(self, project: VideoProject) -> VideoPreferences:
        row = self.db.scalar(select(VideoPreferences).where(VideoPreferences.project_id == project.id,
                                                            VideoPreferences.revision == project.revision))
        if row is None:
            prev = self.db.scalar(select(VideoPreferences).where(VideoPreferences.project_id == project.id)
                                  .order_by(VideoPreferences.revision.desc()).limit(1))
            row = VideoPreferences(project_id=project.id, revision=project.revision, field_sources={})
            if prev is not None:
                P.Prefs.from_row(prev).to_row(row)
            self.db.add(row)
            self.db.flush()
        return row

    def _prefs(self, project: VideoProject) -> P.Prefs:
        return P.Prefs.from_row(self._prefs_row(project))

    def _save_prefs(self, project: VideoProject, prefs: P.Prefs) -> None:
        prefs.to_row(self._prefs_row(project))

    def _status(self, project: VideoProject | None, out: Outbox) -> None:
        if project is None:
            out.text(replies.render("status_none"))
            return
        status = P.label(project.status).lower()
        if project.stage == "planning":
            status = "being planned"
        out.text(replies.render("status", {"title": project.title, "status": status}))

    def _has_completed_job(self, project: VideoProject) -> bool:
        return (self.db.scalar(select(func.count(GenerationJob.id)).where(
            GenerationJob.project_id == project.id, GenerationJob.status == JobStatus.completed.value)) or 0) > 0

    # ================================================================== buttons
    def _handle_button(self, button_id: str, user: User, conv: Conversation, out: Outbox) -> bool:
        parts = button_id.split(":")
        action, target = parts[0], (parts[1] if len(parts) > 1 else "")
        if action == "retry":
            job = self.db.get(GenerationJob, target)
            if job is None or job.user_id != user.id:
                out.text(replies.render("nothing_to_retry"))
                return True
            self.tool_retry(user, self.db.get(VideoProject, job.project_id), out)
            return True
        project = self.db.get(VideoProject, target)
        if project is None or project.user_id != user.id:  # never act on another user's project
            out.text("That button has expired. Send a new message to continue.")
            return True
        out.project_id = project.id
        if action == "confirm":
            self.tool_start_render(user, project, parts[2] if len(parts) > 2 else "", out)
        elif action == "edit":
            out.text("Sure — what should I change? (e.g. \"shorter\", \"start with the beach\", \"no music\")")
        elif action == "cancel":
            self.tool_cancel(user, conv, project, out)
        elif action == "genre" and len(parts) > 2 and project.status == S.awaiting_preferences.value:
            prefs = self._prefs(project)
            P.apply_user_values(prefs, {"genre": parts[2]})
            self._save_prefs(project, prefs)
            self._advance(project, out)
        else:
            return False
        return True

    # ================================================================== tools (side effects)
    def tool_start_render(self, user: User, project: VideoProject, nonce: str | None, out: Outbox) -> GenerationJob | None:
        project = self.db.scalar(select(VideoProject).where(VideoProject.id == project.id).with_for_update())
        key = f"{project.id}:r{project.revision}"
        existing = self.db.scalar(select(GenerationJob).where(GenerationJob.idempotency_key == key))
        if existing is not None:
            out.text(replies.render("already_processing") if existing.status in RUNNING else
                     "That version is already done ✅ Reply *link* for the download link."
                     if existing.status == JobStatus.completed.value else "That version failed — reply *retry*.")
            return None
        if project.status != S.awaiting_confirmation.value or not nonce or nonce != project.confirmation_nonce:
            out.text(replies.render("plan_stale" if project.status == S.awaiting_confirmation.value
                                    else "nothing_to_confirm"))
            return None
        row = self._prefs_row(project)
        if not row.plan or not row.plan.get("items"):
            self._start_planning(project, out, replan=True)
            return None
        today = utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
        if self.db.scalar(select(func.count(GenerationJob.id)).where(
                GenerationJob.user_id == user.id, GenerationJob.created_at >= today)) >= self.s.max_jobs_per_day:
            out.text(replies.render("daily_limit", {"limit": self.s.max_jobs_per_day}))
            return None
        if self.db.scalar(select(func.count(GenerationJob.id)).where(
                GenerationJob.user_id == user.id, GenerationJob.status.in_(list(RUNNING)))):
            out.text("You already have a reel in progress. Reply *yes* again once it arrives.")
            return None
        cost = P.credits_for(row.plan.get("total_seconds"), row.quality)
        job = GenerationJob(project_id=project.id, user_id=user.id, revision=project.revision, idempotency_key=key)
        try:
            with self.db.begin_nested():
                self.db.add(job)
                self.db.flush()
                if not credits.reserve(self.db, job, cost):
                    raise _NoCredits()
        except IntegrityError:
            out.text(replies.render("already_processing"))
            return None
        except _NoCredits:
            self.db.refresh(user)
            key_name = "anon_limit" if user.is_anonymous else "insufficient_credits"
            out.text(replies.render(key_name, {"cost": cost, "balance": user.credits_balance,
                                               "web_url": self.s.web_base_url,
                                               "phone": self._phone_q(self.db.get(Conversation, project.conversation_id))}))
            return None
        project.status = S.queued.value
        project.confirmation_nonce = None
        enqueue(self.db, "render_job", job.id)
        audit(self.db, "job.created", actor_id=user.id, target_type="generation_job", target_id=job.id,
              details={"credits": cost, "revision": project.revision})
        out.text(replies.render("queued"))
        return job

    def tool_cancel(self, user: User, conv: Conversation, project: VideoProject | None, out: Outbox) -> None:
        if project is None or project.user_id != user.id:
            out.text(replies.render("nothing_to_cancel"))
            return
        if project.status in OPEN:
            if project.revision > 1 and self._has_completed_job(project):
                self.db.delete(self._prefs_row(project))
                project.revision -= 1
                project.status, project.confirmation_nonce, project.stage = S.completed.value, None, None
                out.text(replies.render("kept_previous"))
                return
            project.status, project.confirmation_nonce, project.stage = S.cancelled.value, None, None
            conv.active_project_id = None
            out.text(replies.render("cancelled"))
            return
        if project.status == S.queued.value:
            job = self.db.scalar(select(GenerationJob).where(GenerationJob.project_id == project.id,
                                                             GenerationJob.status == JobStatus.queued.value)
                                 .with_for_update())
            if job is not None:
                job.status, job.finished_at = JobStatus.cancelled.value, utcnow()
                refunded = credits.refund(self.db, job)
                project.status = S.cancelled.value
                out.text(replies.render("cancelled_refund", {"credits": refunded}))
                return
        if project.status == S.processing.value:
            out.text(replies.render("cannot_cancel_processing"))
            return
        out.text(replies.render("nothing_to_cancel"))

    def tool_retry(self, user: User, project: VideoProject | None, out: Outbox) -> None:
        if project is None or project.user_id != user.id:
            out.text(replies.render("nothing_to_retry"))
            return
        if project.status == S.awaiting_preferences.value and project.stage == "plan_failed":
            self._start_planning(project, out, replan=True)
            return
        res = retry_failed_job(self.db, project, actor_id=user.id)
        if isinstance(res, str):
            if res == "insufficient_credits":
                self.db.refresh(user)
                out.text(replies.render("insufficient_credits", {
                    "cost": "?", "balance": user.credits_balance, "web_url": self.s.web_base_url}))
            else:
                out.text(replies.render("already_processing" if res == "already_running" else "nothing_to_retry"))
            return
        out.text(replies.render("retry_started"))

    def tool_resend_link(self, user: User, project: VideoProject | None, out: Outbox) -> None:
        q = select(GeneratedAsset).where(GeneratedAsset.user_id == user.id, GeneratedAsset.kind == "video",
                                         GeneratedAsset.deleted_at.is_(None))
        if project is not None:
            q = q.where(GeneratedAsset.project_id == project.id)
        asset = self.db.scalar(q.order_by(GeneratedAsset.created_at.desc()).limit(1))
        if asset is None:
            out.text(replies.render("no_video_yet"))
            return
        out.text(replies.render("resent_link", {"url": sign_asset_url(asset.id),
                                                "hours": self.s.download_url_ttl_seconds // 3600}))


class _NoCredits(Exception):
    pass


def retry_failed_job(db: Session, project: VideoProject, actor_id: str | None) -> GenerationJob | str:
    """Re-run the latest failed job of a project without creating a new paid job."""
    job = db.scalar(select(GenerationJob).where(GenerationJob.project_id == project.id)
                    .order_by(GenerationJob.created_at.desc()).limit(1).with_for_update())
    if job is None:
        return "nothing_to_retry"
    if job.status in (JobStatus.queued.value, JobStatus.processing.value):
        return "already_running"
    if job.status != JobStatus.failed.value or job.revision != project.revision:
        return "nothing_to_retry"
    row = db.scalar(select(VideoPreferences).where(VideoPreferences.project_id == project.id,
                                                   VideoPreferences.revision == job.revision))
    cost = P.credits_for((row.plan or {}).get("total_seconds") if row else 30, row.quality if row else None)
    job.run_number += 1
    job.status, job.attempts, job.stage = JobStatus.queued.value, 0, None
    job.error_code = job.error_message = None
    if not credits.reserve(db, job, cost):
        job.status = JobStatus.failed.value
        job.run_number -= 1
        return "insufficient_credits"
    project.status, project.last_error = S.queued.value, None
    enqueue(db, "render_job", job.id)
    audit(db, "job.retried", actor_id=actor_id, target_type="generation_job", target_id=job.id,
          details={"run": job.run_number})
    return job
