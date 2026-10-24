"""Database schema. Operational fields are relational; JSON only where flexibility helps."""
import enum
import uuid
from datetime import UTC, datetime

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


def utcnow() -> datetime:
    return datetime.now(UTC)


def aware(dt: datetime | None) -> datetime | None:
    """SQLite drops tzinfo; treat naive datetimes as UTC."""
    if dt is not None and dt.tzinfo is None:
        return dt.replace(tzinfo=UTC)
    return dt


def new_id() -> str:
    return str(uuid.uuid4())


class StrEnum(str, enum.Enum):
    def __str__(self) -> str:
        return self.value


class ProjectStatus(StrEnum):
    collecting_media = "collecting_media"
    awaiting_preferences = "awaiting_preferences"
    awaiting_confirmation = "awaiting_confirmation"
    queued = "queued"
    processing = "processing"
    completed = "completed"
    failed = "failed"
    cancelled = "cancelled"


class JobStatus(StrEnum):
    queued = "queued"
    processing = "processing"
    completed = "completed"
    failed = "failed"
    cancelled = "cancelled"


TZ = DateTime(timezone=True)


class User(Base):
    """Registered user, or an anonymous WhatsApp-only profile (is_anonymous=True)."""

    __tablename__ = "users"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    email: Mapped[str | None] = mapped_column(String(320), unique=True, nullable=True)
    name: Mapped[str] = mapped_column(String(120), default="")
    password_hash: Mapped[str | None] = mapped_column(String(255), nullable=True)
    phone_e164: Mapped[str | None] = mapped_column(String(20), index=True)
    is_anonymous: Mapped[bool] = mapped_column(Boolean, default=False)
    role: Mapped[str] = mapped_column(String(20), default="user")
    status: Mapped[str] = mapped_column(String(20), default="active")  # active | suspended
    email_verified_at: Mapped[datetime | None] = mapped_column(TZ)
    credits_balance: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(TZ, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(TZ, default=utcnow, onupdate=utcnow)


class UserSession(Base):
    __tablename__ = "user_sessions"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    csrf_token: Mapped[str] = mapped_column(String(64))
    ip: Mapped[str | None] = mapped_column(String(64))
    user_agent: Mapped[str | None] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(TZ, default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(TZ)
    revoked_at: Mapped[datetime | None] = mapped_column(TZ)


class WhatsAppAccount(Base):
    """A WhatsApp number. user_id points at an anonymous profile until the owner links a website account."""

    __tablename__ = "whatsapp_accounts"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    wa_id: Mapped[str] = mapped_column(String(20), unique=True)
    user_id: Mapped[str | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True)
    status: Mapped[str] = mapped_column(String(20), default="anonymous")  # anonymous | pending | linked
    profile_name: Mapped[str | None] = mapped_column(String(120))
    pending_user_id: Mapped[str | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    link_code_hash: Mapped[str | None] = mapped_column(String(64))
    link_code_expires_at: Mapped[datetime | None] = mapped_column(TZ)
    link_attempts: Mapped[int] = mapped_column(Integer, default=0)
    linked_at: Mapped[datetime | None] = mapped_column(TZ)
    notice_sent_at: Mapped[datetime | None] = mapped_column(TZ)  # privacy/AI notice shown
    last_inbound_at: Mapped[datetime | None] = mapped_column(TZ)
    created_at: Mapped[datetime] = mapped_column(TZ, default=utcnow)


class LoginToken(Base):
    """One-time token behind the website's "Log in with Telegram" link / QR code.

    The deep link `t.me/<bot>?start=login_<token>` is confirmed by the bot; the browser that asked for it redeems
    it once with a secret it alone holds. Only hashes are stored.
    """

    __tablename__ = "login_tokens"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    poll_secret_hash: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(10), default="pending")  # pending | confirmed | consumed | denied
    user_id: Mapped[str | None] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=True)
    expires_at: Mapped[datetime] = mapped_column(TZ)
    created_at: Mapped[datetime] = mapped_column(TZ, default=utcnow)


class Conversation(Base):
    __tablename__ = "conversations"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    whatsapp_account_id: Mapped[str] = mapped_column(ForeignKey("whatsapp_accounts.id", ondelete="CASCADE"), unique=True)
    user_id: Mapped[str | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), index=True, nullable=True)
    active_project_id: Mapped[str | None] = mapped_column(
        ForeignKey("video_projects.id", ondelete="SET NULL", use_alter=True, name="fk_conversations_active_project"),
        nullable=True)
    last_message_at: Mapped[datetime | None] = mapped_column(TZ)
    created_at: Mapped[datetime] = mapped_column(TZ, default=utcnow)

    whatsapp_account: Mapped[WhatsAppAccount] = relationship()


class Message(Base):
    __tablename__ = "messages"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    conversation_id: Mapped[str] = mapped_column(ForeignKey("conversations.id", ondelete="CASCADE"), index=True)
    user_id: Mapped[str | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), index=True, nullable=True)
    project_id: Mapped[str | None] = mapped_column(ForeignKey("video_projects.id", ondelete="SET NULL"), nullable=True)
    direction: Mapped[str] = mapped_column(String(10))  # inbound | outbound
    channel_message_id: Mapped[str | None] = mapped_column(String(128), unique=True)  # wamid => idempotency
    kind: Mapped[str] = mapped_column(String(20))  # text|audio|image|video|interactive|template|link|unsupported
    body: Mapped[str | None] = mapped_column(Text)
    transcript: Mapped[str | None] = mapped_column(Text)
    media_id: Mapped[str | None] = mapped_column(String(128))
    payload: Mapped[dict | None] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String(20))  # in: received|processing|processed|failed|ignored; out: pending|sent|delivered|read|failed
    error: Mapped[str | None] = mapped_column(String(500))
    created_at: Mapped[datetime] = mapped_column(TZ, default=utcnow, index=True)


class VideoProject(Base):
    __tablename__ = "video_projects"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    conversation_id: Mapped[str | None] = mapped_column(ForeignKey("conversations.id", ondelete="SET NULL"), nullable=True)
    title: Mapped[str] = mapped_column(String(200), default="New reel")
    status: Mapped[str] = mapped_column(String(30), default=ProjectStatus.collecting_media.value)
    stage: Mapped[str | None] = mapped_column(String(40))  # e.g. planning, rendering:captions
    revision: Mapped[int] = mapped_column(Integer, default=1)
    confirmation_nonce: Mapped[str | None] = mapped_column(String(32))
    asked: Mapped[list | None] = mapped_column(JSON, default=list)
    last_media_at: Mapped[datetime | None] = mapped_column(TZ)
    batch_closed_at: Mapped[datetime | None] = mapped_column(TZ)
    last_error: Mapped[str | None] = mapped_column(String(500))
    expires_at: Mapped[datetime | None] = mapped_column(TZ)
    created_at: Mapped[datetime] = mapped_column(TZ, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(TZ, default=utcnow, onupdate=utcnow)

    __table_args__ = (Index("ix_video_projects_user_created", "user_id", "created_at"),)


class ProjectAsset(Base):
    """One source clip or photo uploaded by the user."""

    __tablename__ = "project_assets"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(ForeignKey("video_projects.id", ondelete="CASCADE"), index=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    source_message_id: Mapped[str | None] = mapped_column(ForeignKey("messages.id", ondelete="SET NULL"), nullable=True)
    media_id: Mapped[str | None] = mapped_column(String(128))
    kind: Mapped[str] = mapped_column(String(10))  # video | photo
    order_index: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(20), default="pending")  # pending|ready|rejected|failed|deleted
    reject_reason: Mapped[str | None] = mapped_column(String(200))
    storage_key: Mapped[str | None] = mapped_column(String(300))
    mime_type: Mapped[str | None] = mapped_column(String(80))
    size_bytes: Mapped[int] = mapped_column(Integer, default=0)
    duration_seconds: Mapped[float | None] = mapped_column(Float)
    width: Mapped[int | None] = mapped_column(Integer)
    height: Mapped[int | None] = mapped_column(Integer)
    codec: Mapped[str | None] = mapped_column(String(30))
    has_audio: Mapped[bool] = mapped_column(Boolean, default=False)
    caption: Mapped[str | None] = mapped_column(String(500))  # caption the user typed with the media
    quality_score: Mapped[float | None] = mapped_column(Float)
    quality_flags: Mapped[dict | None] = mapped_column(JSON)  # {"dark": true, "duplicate_of": "..."}
    created_at: Mapped[datetime] = mapped_column(TZ, default=utcnow)

    __table_args__ = (UniqueConstraint("project_id", "media_id", name="uq_asset_project_media"),)


class VideoPreferences(Base):
    """Editing preferences + the selection plan, one row per project revision."""

    __tablename__ = "video_preferences"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(ForeignKey("video_projects.id", ondelete="CASCADE"))
    revision: Mapped[int] = mapped_column(Integer, default=1)
    genre: Mapped[str | None] = mapped_column(String(30))
    vibe: Mapped[str | None] = mapped_column(String(30))
    format: Mapped[str | None] = mapped_column(String(30))
    aspect_ratio: Mapped[str] = mapped_column(String(10), default="9:16")
    duration: Mapped[str | None] = mapped_column(String(10))  # original | 15 | 30 | 60
    style: Mapped[str | None] = mapped_column(String(30))
    quality: Mapped[str | None] = mapped_column(String(20))
    captions: Mapped[bool | None] = mapped_column(Boolean)
    music: Mapped[str | None] = mapped_column(String(20))  # original | none | licensed
    narration: Mapped[str | None] = mapped_column(String(20))  # default | none | cloned
    language: Mapped[str | None] = mapped_column(String(20))
    title_text: Mapped[str | None] = mapped_column(String(120))
    instructions: Mapped[str | None] = mapped_column(Text)  # free-form edit requests (trim, emphasise…)
    field_sources: Mapped[dict] = mapped_column(JSON, default=dict)
    plan: Mapped[dict | None] = mapped_column(JSON)  # selection plan + script, shown before confirmation
    playbook_version: Mapped[str | None] = mapped_column(String(40))
    created_at: Mapped[datetime] = mapped_column(TZ, default=utcnow)

    __table_args__ = (UniqueConstraint("project_id", "revision", name="uq_prefs_project_rev"),)


class GenerationJob(Base):
    __tablename__ = "generation_jobs"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(ForeignKey("video_projects.id", ondelete="CASCADE"))
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    revision: Mapped[int] = mapped_column(Integer)
    idempotency_key: Mapped[str] = mapped_column(String(80), unique=True)  # "<project>:r<revision>"
    status: Mapped[str] = mapped_column(String(20), default=JobStatus.queued.value, index=True)
    stage: Mapped[str | None] = mapped_column(String(40))
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    run_number: Mapped[int] = mapped_column(Integer, default=1)
    credits_charged: Mapped[int] = mapped_column(Integer, default=0)
    pipeline: Mapped[dict | None] = mapped_column(JSON)  # which implementation ran each step
    error_code: Mapped[str | None] = mapped_column(String(60))
    error_message: Mapped[str | None] = mapped_column(String(500))
    estimated_cost_usd: Mapped[float] = mapped_column(Numeric(10, 4), default=0)
    created_at: Mapped[datetime] = mapped_column(TZ, default=utcnow)
    started_at: Mapped[datetime | None] = mapped_column(TZ)
    finished_at: Mapped[datetime | None] = mapped_column(TZ)


class GeneratedAsset(Base):
    __tablename__ = "generated_assets"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    project_id: Mapped[str | None] = mapped_column(ForeignKey("video_projects.id", ondelete="CASCADE"), index=True, nullable=True)
    job_id: Mapped[str | None] = mapped_column(ForeignKey("generation_jobs.id", ondelete="SET NULL"), nullable=True)
    kind: Mapped[str] = mapped_column(String(20))  # video | narration | subtitles | script
    storage_key: Mapped[str] = mapped_column(String(300))
    mime_type: Mapped[str] = mapped_column(String(80))
    size_bytes: Mapped[int] = mapped_column(Integer, default=0)
    duration_seconds: Mapped[float | None] = mapped_column(Float)
    width: Mapped[int | None] = mapped_column(Integer)
    height: Mapped[int | None] = mapped_column(Integer)
    checksum_sha256: Mapped[str | None] = mapped_column(String(64))
    is_demo: Mapped[bool] = mapped_column(Boolean, default=False)  # produced with mock/offline providers
    extra: Mapped[dict | None] = mapped_column(JSON)
    expires_at: Mapped[datetime | None] = mapped_column(TZ)
    deleted_at: Mapped[datetime | None] = mapped_column(TZ)
    created_at: Mapped[datetime] = mapped_column(TZ, default=utcnow)


class GenrePlaybook(Base):
    """Curated, versioned editing playbook. Loaded from reviewed files; read-only to the agent."""

    __tablename__ = "genre_playbooks"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    genre: Mapped[str] = mapped_column(String(30))
    version: Mapped[str] = mapped_column(String(40))
    content: Mapped[dict] = mapped_column(JSON)
    checksum: Mapped[str] = mapped_column(String(64))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(TZ, default=utcnow)

    __table_args__ = (UniqueConstraint("genre", "version", name="uq_playbook_genre_version"),)


class VoiceProfile(Base):
    """Phase 2: cloned user voice. Consent is mandatory and recorded."""

    __tablename__ = "voice_profiles"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    provider: Mapped[str] = mapped_column(String(30), default="elevenlabs")
    provider_voice_id: Mapped[str | None] = mapped_column(String(100))
    consent_text: Mapped[str] = mapped_column(Text)
    consent_message_id: Mapped[str | None] = mapped_column(String(36))
    consented_at: Mapped[datetime] = mapped_column(TZ, default=utcnow)
    delete_after: Mapped[datetime | None] = mapped_column(TZ)
    deleted_at: Mapped[datetime | None] = mapped_column(TZ)
    created_at: Mapped[datetime] = mapped_column(TZ, default=utcnow)


class UsageRecord(Base):
    __tablename__ = "usage_records"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    user_id: Mapped[str | None] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=True)
    job_id: Mapped[str | None] = mapped_column(ForeignKey("generation_jobs.id", ondelete="SET NULL"), nullable=True)
    kind: Mapped[str] = mapped_column(String(30), index=True)
    credits_delta: Mapped[int] = mapped_column(Integer, default=0)
    provider: Mapped[str | None] = mapped_column(String(40))
    quantity: Mapped[float] = mapped_column(Float, default=0)
    unit: Mapped[str | None] = mapped_column(String(20))
    estimated_cost_usd: Mapped[float] = mapped_column(Numeric(10, 4), default=0)
    idempotency_key: Mapped[str | None] = mapped_column(String(120), unique=True)
    created_at: Mapped[datetime] = mapped_column(TZ, default=utcnow, index=True)


class AuditLog(Base):
    __tablename__ = "audit_logs"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    actor_user_id: Mapped[str | None] = mapped_column(String(36), index=True)
    action: Mapped[str] = mapped_column(String(60), index=True)
    target_type: Mapped[str | None] = mapped_column(String(40))
    target_id: Mapped[str | None] = mapped_column(String(64))
    details: Mapped[dict | None] = mapped_column(JSON)
    ip: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(TZ, default=utcnow, index=True)
