"""Application settings, loaded from environment variables (never from source code)."""
from functools import lru_cache
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_name: str = "ReelChat"
    app_env: Literal["development", "test", "production"] = "development"
    secret_key: str = Field(default="dev-insecure-secret-change-me", min_length=16)
    database_url: str = "sqlite:///./reelchat.db"
    # Public URL of this API (used to build signed download links).
    public_base_url: str = "http://localhost:8100"
    # Public URL of the website (used in emails and WhatsApp replies).
    web_base_url: str = "http://localhost:3100"
    cors_origins: str = "http://localhost:3100"

    # --- Bootstrap admin (created on startup if both set) ---
    admin_email: str | None = None
    admin_password: str | None = None

    # --- AI agent ---
    # rules | anthropic | openai | openai_compatible | gemini | agenticorg
    agent_provider: str = "rules"
    agent_model: str | None = None  # default model for all tasks
    agent_model_analysis: str | None = None  # intent + requirement extraction
    agent_model_response: str | None = None  # phrasing replies
    agent_model_script: str | None = None  # script writing
    anthropic_api_key: str | None = None
    openai_api_key: str | None = None
    openai_base_url: str | None = None  # for openai_compatible (e.g. Ollama, vLLM)
    gemini_api_key: str | None = None
    agenticorg_api_key: str | None = None
    agenticorg_agent_name: str | None = None  # existing agent UUID or type
    agenticorg_base_url: str | None = None  # SDK default when unset
    agenticorg_company_id: str | None = None  # required for agent-type runs
    agenticorg_action: str = "process"
    agent_timeout_seconds: float = 30.0

    # --- Speech ---
    transcription_provider: str = "mock"  # mock | openai
    transcription_model: str = "gpt-4o-mini-transcribe"
    tts_provider: str = "mock"  # mock | espeak | openai
    tts_model: str = "gpt-4o-mini-tts"

    # --- Video ---
    video_provider: str = "mock"  # mock | stock
    pexels_api_key: str | None = None
    ffmpeg_path: str = "ffmpeg"
    ffprobe_path: str = "ffprobe"
    work_dir: str = "./var/work"

    # --- Messaging (WhatsApp) ---
    messaging_provider: str = "dev"  # dev | meta
    whatsapp_access_token: str | None = None
    whatsapp_phone_number_id: str | None = None
    whatsapp_app_secret: str = "dev-app-secret"
    whatsapp_verify_token: str = "dev-verify-token"
    whatsapp_api_version: str = "v21.0"
    whatsapp_business_number: str = "15550000000"  # shown to users as wa.me link
    whatsapp_notify_template: str = "reelchat_update"  # approved utility template
    whatsapp_template_language: str = "en"
    whatsapp_max_video_bytes: int = 16 * 1024 * 1024

    # --- Storage ---
    storage_backend: str = "local"  # local | s3
    storage_local_dir: str = "./var/storage"
    s3_bucket: str | None = None
    s3_endpoint_url: str | None = None
    s3_region: str = "us-east-1"
    s3_access_key_id: str | None = None
    s3_secret_access_key: str | None = None

    # --- Email ---
    email_provider: str = "console"  # console | smtp
    smtp_host: str | None = None
    smtp_port: int = 587
    smtp_username: str | None = None
    smtp_password: str | None = None
    smtp_from: str = "ReelChat <no-reply@example.com>"

    # --- Limits, quotas, retention ---
    default_credits: int = 10
    max_jobs_per_day: int = 10
    session_ttl_hours: int = 24 * 7
    download_url_ttl_seconds: int = 24 * 3600
    link_code_ttl_minutes: int = 15
    max_voice_note_bytes: int = 16 * 1024 * 1024
    max_inbound_text_chars: int = 2000
    inbound_messages_per_minute: int = 20
    message_retention_days: int = 90
    asset_retention_days: int = 30
    job_max_attempts: int = 3
    job_retry_base_seconds: float = 5.0
    worker_poll_seconds: float = 0.5

    cookie_secure: bool = False

    @property
    def is_dev(self) -> bool:
        return self.app_env in ("development", "test")

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
