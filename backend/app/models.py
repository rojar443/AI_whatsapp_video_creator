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
    draft = "draft"
    awaiting_input = "awaiting_input"
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
    __tablename__ = "users"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    email: Mapped[str] = mapped_column(String(320), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(120))
    password_hash: Mapped[str] = mapped_column(String(255))
    phone_e164: Mapped[str] = mapped_column(String(20), index=True)
    role: Mapped[str] = mapped_column(String(20), default="user")  # user | admin
    status: Mapped[str] = mapped_column(String(20), default="active")  # active | suspended
    email_verified_at: Mapped[datetime | None] = mapped_column(TZ)
    credits_balance: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(TZ, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(TZ, default=utcnow, onupdate=utcnow)

    whatsapp_account: Mapped["WhatsAppAccount | None"] = relationship(
        back_populates="user", foreign_keys="WhatsAppAccount.user_id"
    )


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

    user: Mapped[User] = relationship()


class WhatsAppAccount(Base):
    """A WhatsApp number that has contacted us. user_id is set once ownership is proven."""

    __tablename__ = "whatsapp_accounts"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    wa_id: Mapped[str] = mapped_column(String(20), unique=True)  # digits only, no '+'
    user_id: Mapped[str | None] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), unique=True, nullable=True
    )
    status: Mapped[str] = mapped_column(String(20), default="unlinked")  # unlinked|pending|linked
    profile_name: Mapped[str | None] = mapped_column(String(120))
    # Account that requested a link code; becomes user_id once the code arrives from this number.
    pending_user_id: Mapped[str | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    link_code_hash: Mapped[str | None] = mapped_column(String(64))
    link_code_expires_at: Mapped[datetime | None] = mapped_column(TZ)
    link_attempts: Mapped[int] = mapped_column(Integer, default=0)
    linked_at: Mapped[datetime | None] = mapped_column(TZ)
    opted_in_at: Mapped[datetime | None] = mapped_column(TZ)
    last_inbound_at: Mapped[datetime | None] = mapped_column(TZ)
    last_unlinked_reply_at: Mapped[datetime | None] = mapped_column(TZ)
    created_at: Mapped[datetime] = mapped_column(TZ, default=utcnow)

    user: Mapped[User | None] = relationship(back_populates="whatsapp_account", foreign_keys=[user_id])


class Conversation(Base):
    __tablename__ = "conversations"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    whatsapp_account_id: Mapped[str] = mapped_column(
        ForeignKey("whatsapp_accounts.id", ondelete="CASCADE"), unique=True
    )
    user_id: Mapped[str | None] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=True
    )
    active_project_id: Mapped[str | None] = mapped_column(
        ForeignKey("video_projects.id", ondelete="SET NULL", use_alter=True), nullable=True
    )
    last_message_at: Mapped[datetime | None] = mapped_column(TZ)
    created_at: Mapped[datetime] = mapped_column(TZ, default=utcnow)

    whatsapp_account: Mapped[WhatsAppAccount] = relationship()


class Message(Base):
    __tablename__ = "messages"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    conversation_id: Mapped[str] = mapped_column(
        ForeignKey("conversations.id", ondelete="CASCADE"), index=True
    )
    user_id: Mapped[str | None] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=True
    )
    project_id: Mapped[str | None] = mapped_column(
        ForeignKey("video_projects.id", ondelete="SET NULL"), nullable=True
    )
    direction: Mapped[str] = mapped_column(String(10))  # inbound | outbound
    # Provider message id (wamid). Unique => duplicate webhook deliveries are ignored.
    channel_message_id: Mapped[str | None] = mapped_column(String(128), unique=True)
    kind: Mapped[str] = mapped_column(String(20))  # text|audio|interactive|video|template|link
    body: Mapped[str | None] = mapped_column(Text)
    transcript: Mapped[str | None] = mapped_column(Text)
    media_id: Mapped[str | None] = mapped_column(String(128))
    payload: Mapped[dict | None] = mapped_column(JSON)  # buttons, button reply ids, etc.
    # inbound: received|processed|failed|ignored ; outbound: pending|sent|delivered|read|failed
    status: Mapped[str] = mapped_column(String(20))
    error: Mapped[str | None] = mapped_column(String(500))
    created_at: Mapped[datetime] = mapped_column(TZ, default=utcnow, index=True)


class VideoProject(Base):
    __tablename__ = "video_projects"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    conversation_id: Mapped[str | None] = mapped_column(
        ForeignKey("conversations.id", ondelete="SET NULL"), nullable=True
    )
    title: Mapped[str] = mapped_column(String(200), default="Untitled video")
    status: Mapped[str] = mapped_column(String(30), default=ProjectStatus.draft.value)
    revision: Mapped[int] = mapped_column(Integer, default=1)
    # Changes every time a summary is shown; confirming requires the current nonce.
    confirmation_nonce: Mapped[str | None] = mapped_column(String(32))
    asked_groups: Mapped[list | None] = mapped_column(JSON, default=list)
    last_error: Mapped[str | None] = mapped_column(String(500))
    created_at: Mapped[datetime] = mapped_column(TZ, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(TZ, default=utcnow, onupdate=utcnow)

    __table_args__ = (Index("ix_video_projects_user_created", "user_id", "created_at"),)

    preferences: Mapped[list["VideoPreferences"]] = relationship(
        back_populates="project", cascade="all, delete-orphan", order_by="VideoPreferences.revision"
    )
    jobs: Mapped[list["GenerationJob"]] = relationship(
        back_populates="project", cascade="all, delete-orphan", order_by="GenerationJob.created_at"
    )


class VideoPreferences(Base):
    """One row per project revision. field_sources marks each value user|default|inferred."""

    __tablename__ = "video_preferences"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(ForeignKey("video_projects.id", ondelete="CASCADE"))
    revision: Mapped[int] = mapped_column(Integer, default=1)
    topic: Mapped[str | None] = mapped_column(String(300))
    vibe: Mapped[str | None] = mapped_column(String(30))
    format: Mapped[str | None] = mapped_column(String(30))
    aspect_ratio: Mapped[str] = mapped_column(String(10), default="9:16")
    duration_seconds: Mapped[int | None] = mapped_column(Integer)
    visual_style: Mapped[str | None] = mapped_column(String(30))
    quality: Mapped[str | None] = mapped_column(String(20))
    language: Mapped[str | None] = mapped_column(String(30))
    voice: Mapped[str | None] = mapped_column(String(20))
    captions_enabled: Mapped[bool | None] = mapped_column(Boolean)
    caption_language: Mapped[str | None] = mapped_column(String(30))
    music: Mapped[str | None] = mapped_column(String(30))
    pace: Mapped[str | None] = mapped_column(String(10))
    brand_colors: Mapped[str | None] = mapped_column(String(100))
    call_to_action: Mapped[str | None] = mapped_column(String(200))
    script_mode: Mapped[str | None] = mapped_column(String(10))  # ai | user
    user_script: Mapped[str | None] = mapped_column(Text)
    field_sources: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(TZ, default=utcnow)

    __table_args__ = (UniqueConstraint("project_id", "revision", name="uq_prefs_project_rev"),)

    project: Mapped[VideoProject] = relationship(back_populates="preferences")


class GenerationJob(Base):
    __tablename__ = "generation_jobs"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(ForeignKey("video_projects.id", ondelete="CASCADE"))
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    revision: Mapped[int] = mapped_column(Integer)
    # "<project_id>:r<revision>" — one paid job per approved revision, enforced by the DB.
    idempotency_key: Mapped[str] = mapped_column(String(80), unique=True)
    status: Mapped[str] = mapped_column(String(20), default=JobStatus.queued.value, index=True)
    stage: Mapped[str | None] = mapped_column(String(40))
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    run_number: Mapped[int] = mapped_column(Integer, default=1)  # increments on manual retry
    credits_charged: Mapped[int] = mapped_column(Integer, default=0)
    video_provider: Mapped[str | None] = mapped_column(String(40))
    is_mock: Mapped[bool] = mapped_column(Boolean, default=False)
    error_code: Mapped[str | None] = mapped_column(String(60))
    error_message: Mapped[str | None] = mapped_column(String(500))
    estimated_cost_usd: Mapped[float] = mapped_column(Numeric(10, 4), default=0)
    created_at: Mapped[datetime] = mapped_column(TZ, default=utcnow)
    started_at: Mapped[datetime | None] = mapped_column(TZ)
    finished_at: Mapped[datetime | None] = mapped_column(TZ)

    project: Mapped[VideoProject] = relationship(back_populates="jobs")


class GeneratedAsset(Base):
    __tablename__ = "generated_assets"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    project_id: Mapped[str | None] = mapped_column(
        ForeignKey("video_projects.id", ondelete="CASCADE"), index=True, nullable=True
    )
    job_id: Mapped[str | None] = mapped_column(
        ForeignKey("generation_jobs.id", ondelete="SET NULL"), nullable=True
    )
    kind: Mapped[str] = mapped_column(String(20))  # video | script | subtitles
    storage_key: Mapped[str] = mapped_column(String(300))
    mime_type: Mapped[str] = mapped_column(String(80))
    size_bytes: Mapped[int] = mapped_column(Integer, default=0)
    duration_seconds: Mapped[float | None] = mapped_column(Float)
    width: Mapped[int | None] = mapped_column(Integer)
    height: Mapped[int | None] = mapped_column(Integer)
    checksum_sha256: Mapped[str | None] = mapped_column(String(64))
    is_mock: Mapped[bool] = mapped_column(Boolean, default=False)
    extra: Mapped[dict | None] = mapped_column(JSON)
    expires_at: Mapped[datetime | None] = mapped_column(TZ)
    deleted_at: Mapped[datetime | None] = mapped_column(TZ)
    created_at: Mapped[datetime] = mapped_column(TZ, default=utcnow)


class ProviderConfiguration(Base):
    """Admin overrides of env-configured providers. Holds names/models only, never secrets."""

    __tablename__ = "provider_configurations"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    category: Mapped[str] = mapped_column(String(30))  # agent|video|tts|transcription
    task: Mapped[str] = mapped_column(String(30), default="default")
    provider: Mapped[str] = mapped_column(String(40))
    model: Mapped[str | None] = mapped_column(String(100))
    settings: Mapped[dict | None] = mapped_column(JSON)
    updated_by: Mapped[str | None] = mapped_column(String(36))
    updated_at: Mapped[datetime] = mapped_column(TZ, default=utcnow, onupdate=utcnow)

    __table_args__ = (UniqueConstraint("category", "task", name="uq_provider_cat_task"),)


class UsageRecord(Base):
    """Credit ledger + cost tracking. idempotency_key prevents double charges/refunds."""

    __tablename__ = "usage_records"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    user_id: Mapped[str | None] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=True
    )
    job_id: Mapped[str | None] = mapped_column(
        ForeignKey("generation_jobs.id", ondelete="SET NULL"), nullable=True
    )
    # credit_grant|credit_reserve|credit_refund|credit_adjust|llm|tts|transcription|video
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


class TaskQueue(Base):
    """Durable Postgres-backed task queue (claimed with SELECT ... FOR UPDATE SKIP LOCKED)."""

    __tablename__ = "task_queue"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    task_type: Mapped[str] = mapped_column(String(40))
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String(20), default="pending")  # pending|running|done|dead
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, default=3)
    run_after: Mapped[datetime] = mapped_column(TZ, default=utcnow)
    locked_by: Mapped[str | None] = mapped_column(String(64))
    locked_at: Mapped[datetime | None] = mapped_column(TZ)
    last_error: Mapped[str | None] = mapped_column(String(1000))
    dedupe_key: Mapped[str | None] = mapped_column(String(160), unique=True)
    created_at: Mapped[datetime] = mapped_column(TZ, default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(TZ)

    __table_args__ = (Index("ix_task_queue_claim", "status", "run_after"),)
