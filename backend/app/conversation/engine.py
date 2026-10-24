"""Conversation state machine.

    (none) ──create──▶ awaiting_input ──all groups answered──▶ awaiting_confirmation
                          ▲   │ cancel                              │ explicit yes / button+nonce
                          │   ▼                                     ▼
                       cancelled ◀──cancel (queued only)──── queued ──▶ processing ──▶ completed
                                                                            │              │ modify
                                                                            ▼              ▼
                                                                         failed ──retry──▶ queued
                                                                  (revision n+1 → awaiting_confirmation)

The agent only classifies messages and proposes field values. This module decides every
transition and is the only caller of the "tools" below (start_generation, cancel, retry,
resend_link), each of which checks ownership, state, credits and idempotency itself.
"""
from __future__ import annotations

import logging
from datetime import timedelta

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app import providers
from app.agents.base import AgentContext, AgentProvider, MessageAnalysis
from app.agents.rules import RulesAgentProvider
from app.config import get_settings
from app.conversation import replies
from app.conversation import requirements as req
from app.conversation.safety import is_explicit_confirmation, parse_link_code, parse_login_code
from app.jobs.queue import enqueue
from app.messaging.base import SendError
from app.models import (
    Conversation,
    GeneratedAsset,
    GenerationJob,
    JobStatus,
    Message,
    ProjectStatus,
    User,
    VideoPreferences,
    VideoProject,
    WhatsAppAccount,
    aware,
    utcnow,
)
from app.security import new_token, sha256_hex, sign_asset_url
from app.services import credits, login
from app.services.audit import audit
from app.services.outbox import Outbox
from app.transcription import TranscriptionError, looks_like_audio

log = logging.getLogger(__name__)
ACTIVE_INPUT = (ProjectStatus.awaiting_input.value, ProjectStatus.awaiting_confirmation.value,
                ProjectStatus.draft.value)
RUNNING = (ProjectStatus.queued.value, ProjectStatus.processing.value)


def link_code_hash(wa_id: str, code: str) -> str:
    return sha256_hex(f"{wa_id}:{code}:{get_settings().secret_key}")


class ConversationEngine:
    def __init__(self, db: Session, agent: AgentProvider | None = None):
        self.db = db
        self.settings = get_settings()
        self.agent = agent or providers.get_agent(db)
        self.fallback_agent = RulesAgentProvider()

    # ================================================================== entry point
    def handle_inbound(self, message_id: str) -> None:
        msg = self.db.get(Message, message_id)
        if msg is None or msg.direction != "inbound" or msg.status != "received":
            return  # already handled (idempotent)
        conv = self.db.get(Conversation, msg.conversation_id)
        out = Outbox(self.db, conv)
        try:
            with self.db.begin_nested():
                self._handle(msg, conv, out)
            if msg.status == "received":
                msg.status = "processed"
        except Exception:  # noqa: BLE001 - never leave the user without a reply
            log.exception("failed to handle inbound message %s", message_id)
            out = Outbox(self.db, conv)
            msg.status = "failed"
            msg.error = "internal error"
            out.text("Sorry, something went wrong on my side. Please try again in a moment.")
        out.flush()
        conv.last_message_at = utcnow()
        self.db.commit()

    # ================================================================== dispatch
    def _handle(self, msg: Message, conv: Conversation, out: Outbox) -> None:
        wa: WhatsAppAccount = conv.whatsapp_account
        if self._rate_limited(conv, msg, out):
            return
        login_code = parse_login_code(msg.body or "") if msg.kind == "text" else None
        if login_code:
            self._handle_login(msg, wa, conv, out, login_code)
            return
        user = self.db.get(User, wa.user_id) if wa.user_id else None
        if user is None or wa.status != "linked":
            self._handle_unlinked(msg, wa, conv, out)
            return
        if user.status != "active":
            out.text(replies.render("suspended"))
            return

        text = self._text_of(msg, out, user)
        if text is None:
            return

        if msg.kind == "interactive" and msg.payload and msg.payload.get("button_id"):
            if self._handle_button(msg.payload["button_id"], user, conv, out):
                return
        if parse_link_code(text):
            out.text("This number is already linked to your account ✅")
            return

        project = self.db.get(VideoProject, conv.active_project_id) if conv.active_project_id else None
        if project is not None and project.user_id != user.id:  # defensive tenant check
            project = None
        out.project_id = project.id if project else None
        ctx = self._context(project, conv)
        analysis = self._analyze(text, ctx, user)
        self._dispatch(text, analysis, user, conv, project, out)

    def _dispatch(self, text: str, a: MessageAnalysis, user: User, conv: Conversation,
                  project: VideoProject | None, out: Outbox) -> None:
        state = project.status if project else None
        intent = a.intent
        explicit_yes = is_explicit_confirmation(text)

        if a.unsupported and intent not in ("help", "status", "cancel"):
            out.text(replies.render("unsupported", {"items": "; ".join(a.unsupported)}))

        if intent == "help":
            out.text(replies.render("help"))
            return
        if intent == "status":
            self._status(project, out)
            return
        if intent == "cancel":
            self.tool_cancel(user, conv, project, out)
            return
        if intent == "resend_link":
            self.tool_resend_link(user, project, out)
            return
        if intent == "retry":
            self.tool_retry(user, project, out)
            return
        if intent == "new_project":
            if project and state in ACTIVE_INPUT and not self._has_completed_job(project):
                project.status = ProjectStatus.cancelled.value
            conv.active_project_id = None
            self._new_project(user, conv, {}, out, greet=False)
            return

        if state in ACTIVE_INPUT:
            if state == ProjectStatus.awaiting_confirmation.value and explicit_yes:
                self.tool_start_generation(user, project, project.confirmation_nonce, out)
                return
            if intent == "greeting" and not a.fields:
                out.text(self._pending_prompt(project))
                return
            self._update_requirements(project, a, out, accept_defaults=explicit_yes or intent == "provide_info")
            return

        if state in RUNNING:
            if a.fields or intent in ("modify", "create_video"):
                out.text(replies.render("busy_modify"))
            else:
                self._status(project, out)
            return

        # No active project, or it finished / failed / was cancelled.
        if project and state in (ProjectStatus.completed.value, ProjectStatus.failed.value) and (
            intent == "modify" or (a.is_revision and a.fields)
            or (a.fields and intent not in ("create_video",) and "topic" not in a.fields)
        ):
            self._revise(project, a, out)
            return
        if intent in ("create_video", "provide_info", "modify") and (a.fields or intent == "create_video"):
            self._new_project(user, conv, a.fields, out, greet=False)
            return
        if intent == "greeting":
            out.text(replies.render("welcome", {"name": user.name.split(" ")[0]}))
            return
        if intent == "confirm" and state == ProjectStatus.completed.value:
            out.text("Glad you like it! 🎉 Send another idea whenever you're ready.")
            return
        out.text(self.agent.generate_response("clarify", self._context(project, conv), {"user_text": text}))

    # ================================================================== helpers
    def _rate_limited(self, conv: Conversation, msg: Message, out: Outbox) -> bool:
        since = utcnow() - timedelta(seconds=60)
        n = self.db.scalar(select(func.count(Message.id)).where(
            Message.conversation_id == conv.id, Message.direction == "inbound", Message.created_at >= since))
        limit = self.settings.inbound_messages_per_minute
        if n > limit:
            msg.status = "ignored"
            if n == limit + 1:
                out.text(replies.render("rate_limited"))
            return True
        return False

    def _handle_login(self, msg: Message, wa: WhatsAppAccount, conv: Conversation, out: Outbox, code: str) -> None:
        """`LOGIN <code>` from linked and unlinked senders alike. The number comes from the webhook."""
        web = self.settings.web_base_url
        ch = login.match_code(self.db, wa.wa_id, code)
        if ch is None:
            out.text(replies.render("login_failed", {"web_url": web}))
            return
        owner = self.db.get(User, wa.user_id) if wa.user_id and wa.status == "linked" else None
        if ch.purpose == "reauth":
            if owner is None or owner.id != ch.user_id:
                out.text(replies.render("login_failed", {"web_url": web}))
                return
            user = owner
        elif owner is not None:
            user = owner
        else:
            # Nobody has verified this number yet: the first to prove it owns it (this message) claims it.
            # An email account that merely typed the number is deliberately not matched.
            user = User(email=None, password_hash=None, name=(wa.profile_name or "ReelChat user")[:120],
                        phone_e164="+" + wa.wa_id, credits_balance=0)
            self.db.add(user)
            self.db.flush()
            credits.grant(self.db, user, self.settings.default_credits, key=f"signup:{user.id}")
            wa.user_id, wa.status = user.id, "linked"
            wa.linked_at = wa.opted_in_at = utcnow()
            wa.pending_user_id = wa.link_code_hash = wa.link_code_expires_at = None
            wa.link_attempts = 0
            conv.user_id = out.conv.user_id = user.id
            audit(self.db, "user.registered_whatsapp", actor_id=user.id, target_type="user", target_id=user.id)
            audit(self.db, "whatsapp.linked", actor_id=user.id, target_type="whatsapp_account", target_id=wa.id)
        msg.user_id = user.id
        if user.status != "active":
            ch.status = "denied"
            out.text(replies.render("login_denied"))
            return
        ch.status, ch.user_id = "confirmed", user.id
        audit(self.db, "auth.login_confirmed" if ch.purpose == "login" else "auth.reauth_confirmed",
              actor_id=user.id, target_type="login_challenge", target_id=ch.id)
        out.text(replies.render("login_confirmed"))

    def _handle_unlinked(self, msg: Message, wa: WhatsAppAccount, conv: Conversation, out: Outbox) -> None:
        code = parse_link_code(msg.body or "") if msg.kind == "text" else None
        web = self.settings.web_base_url
        if code:
            exp = aware(wa.link_code_expires_at)
            ok = (wa.pending_user_id and wa.link_code_hash and exp and exp > utcnow()
                  and wa.link_attempts < 5 and wa.link_code_hash == link_code_hash(wa.wa_id, code))
            if not ok:
                wa.link_attempts += 1
                out.text(replies.render("link_failed", {"web_url": web}))
                return
            user = self.db.get(User, wa.pending_user_id)
            if user is None or user.status != "active":
                out.text(replies.render("link_failed", {"web_url": web}))
                return
            # One WhatsApp number per account: release any other number linked to this user.
            other = self.db.scalar(select(WhatsAppAccount).where(
                WhatsAppAccount.user_id == user.id, WhatsAppAccount.id != wa.id))
            if other:
                other.user_id, other.status = None, "unlinked"
                self.db.flush()
            wa.user_id = user.id
            wa.status = "linked"
            wa.linked_at = wa.opted_in_at = utcnow()
            wa.pending_user_id = wa.link_code_hash = wa.link_code_expires_at = None
            wa.link_attempts = 0
            conv.user_id = user.id
            msg.user_id = user.id
            audit(self.db, "whatsapp.linked", actor_id=user.id, target_type="whatsapp_account", target_id=wa.id)
            out.conv.user_id = user.id
            out.text(replies.render("linked", {"name": user.name.split(" ")[0], "web_url": web}))
            return
        last = aware(wa.last_unlinked_reply_at)
        if last is None or utcnow() - last > timedelta(minutes=10):
            wa.last_unlinked_reply_at = utcnow()
            out.text(replies.render("not_linked", {"web_url": web}))
        msg.status = "ignored"

    def _text_of(self, msg: Message, out: Outbox, user: User) -> str | None:
        if msg.kind in ("text", "interactive"):
            return req.clean_text(msg.body or "", self.settings.max_inbound_text_chars) or ""
        if msg.kind == "audio":
            return self._transcribe(msg, out)
        out.text(replies.render("unsupported_media"))
        msg.status = "ignored"
        return None

    def _transcribe(self, msg: Message, out: Outbox) -> str | None:
        messaging = providers.get_messaging()
        try:
            data, mime = messaging.download_media(msg.media_id or "")
        except SendError:
            out.text(replies.render("voice_failed"))
            msg.status, msg.error = "failed", "media download failed"
            return None
        if len(data) > self.settings.max_voice_note_bytes:
            out.text(replies.render("voice_too_large"))
            msg.status, msg.error = "failed", "voice note too large"
            return None
        if not looks_like_audio(data, mime):
            out.text(replies.render("unsupported_media"))
            msg.status, msg.error = "failed", "not an audio file"
            return None
        transcriber = providers.get_transcriber(self.db)
        try:
            result = transcriber.transcribe(data, mime)
        except TranscriptionError as e:
            out.text(replies.render("voice_failed"))
            msg.status, msg.error = "failed", str(e)[:200]
            return None
        finally:
            self._discard_media(msg.media_id)
        credits.record_cost(self.db, user_id=msg.user_id, kind="transcription", provider=transcriber.name,
                            quantity=result.seconds or 0, unit="seconds",
                            cost_usd=(result.seconds or 0) / 60 * (0.003 if transcriber.name == "openai" else 0))
        text = req.clean_text(result.text, self.settings.max_inbound_text_chars) or ""
        msg.transcript = text  # stored for context; never written to logs
        return text

    @staticmethod
    def _discard_media(media_id: str | None) -> None:
        """We don't keep voice recordings after transcription."""
        if not media_id:
            return
        from app.storage import get_storage

        storage = get_storage()
        for key in (f"dev-media/{media_id}", f"dev-media/{media_id}.mime"):
            try:
                if storage.exists(key):
                    storage.delete(key)
            except Exception:  # noqa: BLE001
                pass

    def _analyze(self, text: str, ctx: AgentContext, user: User) -> MessageAnalysis:
        agent = self.agent
        try:
            agent.last_usage = []
            a = agent.analyze_message(text, ctx)
        except Exception as e:  # noqa: BLE001 - provider outage => deterministic fallback
            log.warning("agent %s failed (%s); falling back to rules", agent.name, type(e).__name__)
            a = self.fallback_agent.analyze_message(text, ctx)
        for u in agent.last_usage:
            credits.record_cost(self.db, user_id=user.id, kind="llm", provider=u.provider,
                                quantity=u.input_tokens + u.output_tokens, unit="tokens",
                                cost_usd=credits.llm_cost(u.model, u.input_tokens, u.output_tokens))
        return a.sanitized()

    def _context(self, project: VideoProject | None, conv: Conversation) -> AgentContext:
        prefs = self._prefs(project) if project else req.Prefs()
        pending = None
        if project and project.status == ProjectStatus.awaiting_input.value and project.asked_groups:
            pending = project.asked_groups[-1]
        recent_rows = self.db.scalars(
            select(Message).where(Message.conversation_id == conv.id)
            .order_by(Message.created_at.desc()).limit(6)).all()
        recent = [("user" if m.direction == "inbound" else "assistant", (m.transcript or m.body or "")[:300])
                  for m in reversed(recent_rows)]
        return AgentContext(state=project.status if project else "none", pending_group=pending,
                            prefs=dict(prefs.values), recent=recent)

    def _prefs_row(self, project: VideoProject) -> VideoPreferences:
        row = self.db.scalar(select(VideoPreferences).where(
            VideoPreferences.project_id == project.id, VideoPreferences.revision == project.revision))
        if row is None:
            row = VideoPreferences(project_id=project.id, revision=project.revision, field_sources={})
            self.db.add(row)
            self.db.flush()
        return row

    def _prefs(self, project: VideoProject) -> req.Prefs:
        return req.Prefs.from_row(self._prefs_row(project))

    def _save_prefs(self, project: VideoProject, prefs: req.Prefs) -> None:
        row = self._prefs_row(project)
        prefs.to_row(row)
        if prefs.get("topic"):
            project.title = str(prefs.get("topic"))[:200].capitalize()

    def _has_completed_job(self, project: VideoProject) -> bool:
        return self.db.scalar(select(func.count(GenerationJob.id)).where(
            GenerationJob.project_id == project.id, GenerationJob.status == JobStatus.completed.value)) > 0

    def _pending_prompt(self, project: VideoProject) -> str:
        if project.status == ProjectStatus.awaiting_confirmation.value:
            return replies.render("confirm_hint")
        group = (project.asked_groups or ["topic"])[-1]
        return replies.render(f"ask_{group}", {"topic": self._prefs(project).get("topic")})

    def _status(self, project: VideoProject | None, out: Outbox) -> None:
        if project is None:
            out.text(replies.render("status_none"))
            return
        out.text(replies.render("status", {"title": project.title, "status": project.status}))

    # ================================================================== requirement collection
    def _new_project(self, user: User, conv: Conversation, fields: dict, out: Outbox, greet: bool) -> None:
        project = VideoProject(user_id=user.id, conversation_id=conv.id,
                               status=ProjectStatus.awaiting_input.value, asked_groups=[])
        self.db.add(project)
        self.db.flush()
        conv.active_project_id = project.id
        out.project_id = project.id
        prefs = req.Prefs()
        req.apply_user_values(prefs, fields)
        self._save_prefs(project, prefs)
        if not fields:
            project.asked_groups = ["topic"]
            out.text(replies.render("ack_new_project"))
            return
        self._advance(project, prefs, out, [])

    def _update_requirements(self, project: VideoProject, a: MessageAnalysis, out: Outbox,
                             accept_defaults: bool) -> None:
        prefs = self._prefs(project)
        changed = req.apply_user_values(prefs, a.fields)
        if project.status == ProjectStatus.awaiting_confirmation.value:
            if not changed:
                out.text(replies.render("no_changes_detected") + "\n" + replies.render("confirm_hint"))
                return
            self._to_confirmation(project, prefs, out, [])
            return
        self._save_prefs(project, prefs)
        self._advance(project, prefs, out, [])

    def _advance(self, project: VideoProject, prefs: req.Prefs, out: Outbox, notes: list[str]) -> None:
        asked = list(project.asked_groups or [])
        group = req.next_question_group(prefs, asked)
        if group:
            project.status = ProjectStatus.awaiting_input.value
            if group not in asked:
                asked.append(group)
            project.asked_groups = asked
            self._save_prefs(project, prefs)
            out.text(replies.render(f"ask_{group}", {"topic": prefs.get("topic")}))
            return
        self._to_confirmation(project, prefs, out, notes)

    def _to_confirmation(self, project: VideoProject, prefs: req.Prefs, out: Outbox, notes: list[str],
                         intro: str | None = None) -> None:
        req.apply_defaults(prefs)
        video = providers.get_video_provider(self.db)
        result = self.agent.validate_requirements(prefs, video.capabilities())
        self._save_prefs(project, prefs)
        project.status = ProjectStatus.awaiting_confirmation.value
        project.confirmation_nonce = new_token(8)[:12]
        user = self.db.get(User, project.user_id)
        cost = req.credits_for(prefs.get("duration_seconds"), prefs.get("quality"))
        summary = self.agent.summarize_requirements(prefs, cost, user.credits_balance, notes + result.notes)
        if intro:
            summary = intro + "\n\n" + summary
        out.buttons(summary, [
            (f"confirm:{project.id}:{project.confirmation_nonce}", "✅ Generate"),
            (f"edit:{project.id}", "✏️ Change"),
            (f"cancel:{project.id}", "✖ Cancel"),
        ])

    def _revise(self, project: VideoProject, a: MessageAnalysis, out: Outbox) -> None:
        current = self._prefs(project)
        before = current.copy()
        changed = req.apply_user_values(current, a.fields)
        if not changed:
            out.text(replies.render("no_changes_detected"))
            return
        project.revision += 1
        project.asked_groups = []
        self._save_prefs(project, current)
        changes = req.describe_changes(before, current, changed)
        intro = replies.render("revision_summary_intro", {"revision": project.revision, "changes": changes})
        self._to_confirmation(project, current, out, [], intro=intro)

    # ================================================================== buttons
    def _handle_button(self, button_id: str, user: User, conv: Conversation, out: Outbox) -> bool:
        parts = button_id.split(":")
        action = parts[0]
        target = parts[1] if len(parts) > 1 else ""
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
            nonce = parts[2] if len(parts) > 2 else ""
            self.tool_start_generation(user, project, nonce, out)
            return True
        if action == "edit":
            out.text("Sure — what would you like to change? (e.g. \"60 seconds\", \"funny vibe\", \"no music\")")
            return True
        if action == "cancel":
            self.tool_cancel(user, conv, project, out)
            return True
        return False

    # ================================================================== tools (side effects)
    def tool_start_generation(self, user: User, project: VideoProject, nonce: str | None, out: Outbox) -> GenerationJob | None:
        project = self.db.scalar(select(VideoProject).where(VideoProject.id == project.id).with_for_update())
        key = f"{project.id}:r{project.revision}"
        existing = self.db.scalar(select(GenerationJob).where(GenerationJob.idempotency_key == key))
        if existing is not None:
            if existing.status in (JobStatus.queued.value, JobStatus.processing.value):
                out.text(replies.render("already_generating"))
            elif existing.status == JobStatus.completed.value:
                out.text("That version is already done ✅ Reply *link* for the download link.")
            else:
                out.text("That version failed earlier — reply *retry* to run it again.")
            return None
        if project.status != ProjectStatus.awaiting_confirmation.value or not nonce \
                or nonce != project.confirmation_nonce:
            out.text("That summary is out of date. " + replies.render("confirm_hint")
                     if project.status == ProjectStatus.awaiting_confirmation.value
                     else "There's nothing waiting for confirmation right now.")
            return None
        prefs = self._prefs(project)
        if not prefs.get("topic"):
            self._advance(project, prefs, out, [])
            return None

        today = utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
        jobs_today = self.db.scalar(select(func.count(GenerationJob.id)).where(
            GenerationJob.user_id == user.id, GenerationJob.created_at >= today))
        if jobs_today >= self.settings.max_jobs_per_day:
            out.text(replies.render("daily_limit", {"limit": self.settings.max_jobs_per_day}))
            return None
        running = self.db.scalar(select(func.count(GenerationJob.id)).where(
            GenerationJob.user_id == user.id,
            GenerationJob.status.in_([JobStatus.queued.value, JobStatus.processing.value])))
        if running:
            out.text("You already have a video generating. I'll start this one when it's done — "
                     "reply *yes* again after it arrives.")
            return None

        cost = req.credits_for(prefs.get("duration_seconds"), prefs.get("quality"))
        video = providers.get_video_provider(self.db)
        job = GenerationJob(project_id=project.id, user_id=user.id, revision=project.revision,
                            idempotency_key=key, status=JobStatus.queued.value,
                            video_provider=video.name, is_mock=video.is_mock)
        try:
            with self.db.begin_nested():
                self.db.add(job)
                self.db.flush()
                if not credits.reserve(self.db, job, cost):
                    raise _InsufficientCredits()
        except IntegrityError:
            out.text(replies.render("already_generating"))
            return None
        except _InsufficientCredits:
            self.db.refresh(user)
            out.text(replies.render("insufficient_credits", {
                "cost": cost, "balance": user.credits_balance, "web_url": self.settings.web_base_url}))
            return None
        project.status = ProjectStatus.queued.value
        project.confirmation_nonce = None
        enqueue(self.db, "generate_video", {"job_id": job.id}, dedupe_key=f"gen:{job.id}:{job.run_number}")
        audit(self.db, "job.created", actor_id=user.id, target_type="generation_job", target_id=job.id,
              details={"credits": cost, "revision": project.revision})
        out.text(replies.render("queued"))
        return job

    def tool_cancel(self, user: User, conv: Conversation, project: VideoProject | None, out: Outbox) -> None:
        if project is None or project.user_id != user.id:
            out.text(replies.render("nothing_to_cancel"))
            return
        if project.status in ACTIVE_INPUT:
            if project.revision > 1 and self._has_completed_job(project):
                # Abandon the pending revision; keep the delivered video.
                row = self._prefs_row(project)
                self.db.delete(row)
                project.revision -= 1
                project.status = ProjectStatus.completed.value
                project.confirmation_nonce = None
                out.text("Okay, I've kept your previous version unchanged.")
                return
            project.status = ProjectStatus.cancelled.value
            project.confirmation_nonce = None
            conv.active_project_id = None
            out.text(replies.render("cancelled"))
            return
        if project.status == ProjectStatus.queued.value:
            job = self.db.scalar(select(GenerationJob).where(
                GenerationJob.project_id == project.id, GenerationJob.status == JobStatus.queued.value
            ).with_for_update())
            if job is not None:
                job.status = JobStatus.cancelled.value
                job.finished_at = utcnow()
                refunded = credits.refund(self.db, job)
                project.status = ProjectStatus.cancelled.value
                out.text(replies.render("cancelled_refund", {"credits": refunded}))
                return
        if project.status == ProjectStatus.processing.value:
            out.text(replies.render("cannot_cancel_processing"))
            return
        out.text(replies.render("nothing_to_cancel"))

    def tool_retry(self, user: User, project: VideoProject | None, out: Outbox) -> GenerationJob | None:
        if project is None or project.user_id != user.id:
            out.text(replies.render("nothing_to_retry"))
            return None
        job = retry_failed_job(self.db, project, actor_id=user.id)
        if isinstance(job, str):
            if job == "insufficient_credits":
                self.db.refresh(user)
                out.text(replies.render("insufficient_credits", {
                    "cost": req.credits_for(self._prefs(project).get("duration_seconds"),
                                            self._prefs(project).get("quality")),
                    "balance": user.credits_balance, "web_url": self.settings.web_base_url}))
            elif job == "already_running":
                out.text(replies.render("already_generating"))
            else:
                out.text(replies.render("nothing_to_retry"))
            return None
        out.project_id = project.id
        out.text(replies.render("retry_started"))
        return job

    def tool_resend_link(self, user: User, project: VideoProject | None, out: Outbox) -> None:
        q = select(GeneratedAsset).where(GeneratedAsset.user_id == user.id, GeneratedAsset.kind == "video",
                                         GeneratedAsset.deleted_at.is_(None))
        if project is not None:
            q = q.where(GeneratedAsset.project_id == project.id)
        asset = self.db.scalar(q.order_by(GeneratedAsset.created_at.desc()).limit(1))
        if asset is None:
            out.text(replies.render("no_video_yet"))
            return
        hours = self.settings.download_url_ttl_seconds // 3600
        out.text(replies.render("resent_link", {"url": sign_asset_url(asset.id), "hours": hours}))


class _InsufficientCredits(Exception):
    pass


def retry_failed_job(db: Session, project: VideoProject, actor_id: str | None) -> GenerationJob | str:
    """Re-run the latest failed job of a project without creating a new paid job.

    Returns the job, or a reason string: nothing_to_retry | already_running | insufficient_credits.
    """
    job = db.scalar(select(GenerationJob).where(GenerationJob.project_id == project.id)
                    .order_by(GenerationJob.created_at.desc()).limit(1).with_for_update())
    if job is None:
        return "nothing_to_retry"
    if job.status in (JobStatus.queued.value, JobStatus.processing.value):
        return "already_running"
    if job.status != JobStatus.failed.value or job.revision != project.revision:
        return "nothing_to_retry"
    cost = req.credits_for(*_cost_inputs(db, project, job.revision))
    job.run_number += 1
    job.status = JobStatus.queued.value
    job.attempts = 0
    job.error_code = job.error_message = None
    job.stage = None
    if not credits.reserve(db, job, cost):
        job.status = JobStatus.failed.value
        job.run_number -= 1
        return "insufficient_credits"
    project.status = ProjectStatus.queued.value
    project.last_error = None
    enqueue(db, "generate_video", {"job_id": job.id}, dedupe_key=f"gen:{job.id}:{job.run_number}")
    audit(db, "job.retried", actor_id=actor_id, target_type="generation_job", target_id=job.id,
          details={"run": job.run_number})
    return job


def _cost_inputs(db: Session, project: VideoProject, revision: int) -> tuple[int | None, str | None]:
    row = db.scalar(select(VideoPreferences).where(VideoPreferences.project_id == project.id,
                                                   VideoPreferences.revision == revision))
    return (row.duration_seconds, row.quality) if row else (30, "standard")
