from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import text

from app.api import auth, dev, me, projects, webhooks
from app.config import get_settings
from app.db import get_engine, get_sessionmaker
from app.logging_setup import setup_logging

log = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(_: FastAPI):
    setup_logging()
    s = get_settings()
    if s.app_env == "production":
        if s.secret_key.startswith("dev-"):
            raise RuntimeError("Set SECRET_KEY in production")
        if s.whatsapp_app_secret == "dev-app-secret" or s.whatsapp_verify_token == "dev-verify-token":
            raise RuntimeError("Set WHATSAPP_APP_SECRET and WHATSAPP_VERIFY_TOKEN in production")
    try:
        from app import playbooks

        with get_sessionmaker()() as db:
            playbooks.sync_to_db(db)
    except Exception:  # noqa: BLE001 - DB may not be migrated yet; playbooks are also read from files
        log.exception("playbook sync skipped")
    yield


def create_app() -> FastAPI:
    s = get_settings()
    app = FastAPI(title="ReelChat API", version="0.2.0", description="WhatsApp-first AI reel editor",
                  lifespan=lifespan, docs_url="/api/docs", openapi_url="/api/openapi.json", redoc_url=None)
    app.add_middleware(CORSMiddleware, allow_origins=s.cors_origin_list, allow_credentials=True,
                       allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
                       allow_headers=["content-type", "x-csrf-token"])

    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        response = await call_next(request)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
        response.headers.setdefault("X-Frame-Options", "DENY")
        if s.cookie_secure:
            response.headers.setdefault("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
        return response

    @app.exception_handler(Exception)
    async def unhandled(request: Request, exc: Exception):
        log.exception("unhandled error on %s", request.url.path)
        return JSONResponse(status_code=500, content={"detail": "Internal error"})

    for r in (auth.router, me.router, projects.router, webhooks.router, dev.router):
        app.include_router(r)

    @app.get("/api/health", tags=["health"])
    def health():
        try:
            with get_engine().connect() as c:
                c.execute(text("SELECT 1"))
            db_ok = True
        except Exception:  # noqa: BLE001
            db_ok = False
        return {"ok": db_ok, "db": db_ok, "env": s.app_env}

    @app.get("/api/health/providers", tags=["health"])
    def provider_health():
        """Which AI/TTS/messaging providers and pipeline steps are configured, and whether they respond."""
        from app import providers

        if not s.is_dev:
            return {"detail": "available in development only"}
        return providers.health_report()

    @app.get("/api/config", tags=["health"])
    def public_config():
        return {"app_name": s.app_name, "dev_mode": s.is_dev, "whatsapp_enabled": s.whatsapp_enabled,
                "whatsapp_business_number": s.whatsapp_business_number,
                "telegram_bot": s.telegram_bot_username if s.telegram_bot_token else None}

    return app


app = create_app()
