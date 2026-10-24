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
    redis_url: str = "redis://localhost:6379/0"
    # "celery" in docker/production; "inline" runs tasks in-process (tests / no Redis).
    task_backend: Literal["celery", "inline"] = "celery"
    public_base_url: str = "http://localhost:8100"  # public URL of the API (signed links, HTTP steps)
    web_base_url: str = "http://localhost:3100"
    cors_origins: str = "http://localhost:3100"
    cookie_secure: bool = False

    # --- Bootstrap admin (created on startup if both set) ---
    admin_email: str | None = None
    admin_password: str | None = None

    # --- AI provider ---
    # gemini | rules | openai | openai_compatible | anthropic | agenticorg
    ai_provider: str = "rules"
    ai_model: str = "gemini-flash-latest"
    ai_model_vision: str | None = None
    ai_model_script: str | None = None
    ai_timeout_seconds: float = 60.0
    gemini_api_key: str | None = None
    openai_api_key: str | None = None
    openai_base_url: str | None = None
    anthropic_api_key: str | None = None

    # Optional AgenticOrg provider compatibility settings
    agent_provider: str = "rules"
    agent_model: str | None = None  # default model for all tasks
    agent_model_analysis: str | None = None  # intent + requirement extraction
    agent_model_response: str | None = None  # phrasing replies
    agent_model_script: str | None = None  # script writing
    agenticorg_api_key: str | None = None
    agenticorg_agent_name: str | None = None  # existing agent UUID or type
    agenticorg_base_url: str | None = "https://agenticorg.hackathon.pinelabs.com"
    agenticorg_company_id: str | None = None  # required for agent-type runs
    agenticorg_action: str = "process"
    agent_timeout_seconds: float = 30.0

    # --- Speech ---
    transcription_provider: str = "gemini"  # gemini | mock
    tts_provider: str = "elevenlabs"  # elevenlabs | espeak | mock
    elevenlabs_api_key: str | None = None
    elevenlabs_voice_id: str = "21m00Tcm4TlvDq8ikWAM"
    elevenlabs_model_id: str = "eleven_multilingual_v2"
    elevenlabs_base_url: str = "https://api.elevenlabs.io"

    # --- Processing pipeline: each step is "builtin", "python:pkg.mod:Class" or an http(s) URL ---
    pipeline_config_file: str | None = None  # optional YAML with the same keys as below
    pipeline_quality_filter: str = "builtin"
    pipeline_scene_split: str = "builtin"
    pipeline_moment_scoring: str = "builtin"  # builtin = vision model via AI provider, heuristic fallback
    pipeline_sequencing: str = "builtin"
    pipeline_script: str = "builtin"
    pipeline_narration: str = "builtin"
    pipeline_captions: str = "builtin"
    pipeline_music: str = "builtin"
    pipeline_render: str = "builtin"
    pipeline_http_token: str | None = None  # Bearer token sent to HTTP step services
    pipeline_http_timeout_seconds: float = 600
    pipeline_fallback_to_builtin: bool = True
    playbook_dir: str = "./playbooks"
    music_library_dir: str = "./music"
    ffmpeg_path: str = "ffmpeg"
    ffprobe_path: str = "ffprobe"
    work_dir: str = "./var/work"
    ffmpeg_threads: int = 2

    # --- Messaging (WhatsApp) ---
    messaging_provider: str = "dev"  # dev | meta
    whatsapp_access_token: str | None = None
    whatsapp_phone_number_id: str | None = None
    whatsapp_business_account_id: str | None = None
    whatsapp_app_secret: str = "dev-app-secret"
    whatsapp_verify_token: str = "dev-verify-token"
    whatsapp_api_version: str = "v21.0"
    whatsapp_business_number: str = "15550000000"
    whatsapp_notify_template: str = "hello_world"
    whatsapp_template_language: str = "en_US"
    whatsapp_template_params: int = 0  # number of {{n}} body variables in the template
    whatsapp_max_video_bytes: int = 16 * 1024 * 1024
    delivery_target_bytes: int = 15 * 1024 * 1024  # leave headroom under the 16 MB limit

    # --- Telegram (optional second channel; long polling, no public URL needed) ---
    telegram_bot_token: str | None = None
    telegram_poll_timeout: int = 25
    telegram_bot_username: str | None = None  # shown on the website (public, not a secret)
    registration_enabled: bool = False  # Telegram-only use needs no accounts; true re-enables website signup
    whatsapp_enabled: bool = False  # Telegram-first: WhatsApp UI/linking is off unless enabled

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

    # --- Intake limits ---
    max_files_per_project: int = 20
    max_video_bytes: int = 100 * 1024 * 1024
    max_image_bytes: int = 20 * 1024 * 1024
    max_clip_seconds: float = 180
    max_image_pixels: int = 50_000_000
    max_voice_note_bytes: int = 16 * 1024 * 1024
    max_inbound_text_chars: int = 2000
    batch_ack_delay_seconds: float = 4
    batch_timeout_seconds: float = 90
    inbound_messages_per_minute: int = 40

    # --- Quotas, retention ---
    default_credits: int = 10
    anon_credits: int = 2
    anon_max_projects: int = 2
    max_jobs_per_day: int = 10
    session_ttl_hours: int = 24 * 7
    download_url_ttl_seconds: int = 24 * 3600
    link_code_ttl_minutes: int = 15
    # Comma-separated Telegram user ids that get the admin role (e.g. "123456789,987654321").
    admin_telegram_ids: str = ""
    message_retention_days: int = 90
    asset_retention_days: int = 30
    anon_retention_days: int = 3
    source_media_retention_days: int = 7
    job_max_attempts: int = 3
    job_retry_base_seconds: float = 10.0

    @property
    def admin_telegram_id_set(self) -> set[str]:
        return {i.strip() for i in self.admin_telegram_ids.split(",") if i.strip()}

    @property
    def is_dev(self) -> bool:
        return self.app_env in ("development", "test")

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
