# Setup, running, tests and deployment

For the Render deployment with managed PostgreSQL, FFmpeg and persistent local
video storage, see [RENDER.md](RENDER.md).

## Native Windows setup (PostgreSQL only)

This checkout is configured in the repository-root `.env` for native local development:

* PostgreSQL: `127.0.0.1:55433`, app database `reelchat`, disposable test database `reelchat_test`.
* Redis: `127.0.0.1:56379`, with Celery background jobs.
* FFmpeg and FFprobe: verified Windows essentials build under `.tools/ffmpeg/`, with absolute executable paths in `.env`.
* TTS: `mock` (silent demo audio). Install espeak-ng before selecting `espeak`, or configure ElevenLabs for narration.

The project database cluster lives in `.tools/postgres/data`; it is independent of the existing PostgreSQL Windows service on port 5433. Local passwords are generated and stored only in `.env`. `.tools/` and `.env` are ignored by Git. Settings always read the root `.env`, including when commands run from `backend/`.

Start from PowerShell in the repository root:

```powershell
.\scripts\start-local.ps1
# Website: http://localhost:3100
# API docs: http://localhost:8100/api/docs
# Offline simulator: http://localhost:3100/dev/whatsapp
.\scripts\stop-local.ps1
```

The startup script uses the installed PostgreSQL 18 binaries and Redis executable; pass `-PostgresBin` or `-RedisServer` to change their locations. It runs migrations and starts the API, Windows-compatible Celery solo worker, separate scheduler, and frontend in hidden processes. Logs are in `.tools/runtime/`. PostgreSQL remains running when app processes stop.

Run validation with the local PostgreSQL instance running:

```powershell
cd backend
.\.venv\Scripts\python.exe -m alembic check
.\.venv\Scripts\python.exe -m pytest --basetemp=../.tools/pytest-local
```

`TEST_DATABASE_URL` must point to a separate PostgreSQL database whose name ends in `_test`; pytest deletes and recreates its tables. Do not run concurrent test sessions against the same database. The full suite exercises real FFmpeg rendering when the configured executables are available. When provisioning a fresh checkout, create both databases before migrations/tests; `start-local.ps1` expects the local tools and cluster to have been installed.

## 1. Local demo mode (no accounts needed)
Runs fully offline with the rules agent, a silent placeholder voice, sample footage and the WhatsApp simulator.
For an audible offline voice build with `docker compose build --build-arg APT_EXTRA=espeak-ng` and set `TTS_PROVIDER=espeak`.

```bash
cp .env.example .env
# offline demo values:
sed -i 's/^AI_PROVIDER=.*/AI_PROVIDER=rules/; s/^TTS_PROVIDER=.*/TTS_PROVIDER=mock/; s/^TRANSCRIPTION_PROVIDER=.*/TRANSCRIPTION_PROVIDER=mock/' .env
docker compose up --build -d          # postgres, redis, api (:8100), worker, web (:3100)
docker compose logs -f api worker     # watch
```
Open http://localhost:3100/dev/whatsapp → 📎 → **Send sample footage** → type
*"That's all. Make it a cinematic travel reel with a calm voiceover."* → **✅ Proceed**.
Demo output is labelled ("Demo mode: …") because offline stand-ins replace the real AI services.

## 2. Real services (Gemini + ElevenLabs + WhatsApp)
1. Follow [WHATSAPP_SETUP.md](WHATSAPP_SETUP.md) for the Meta app, test number, token, webhook and tunnel.
2. In `.env`: `AI_PROVIDER=gemini`, `GEMINI_API_KEY`, `TRANSCRIPTION_PROVIDER=gemini`,
   `TTS_PROVIDER=elevenlabs`, `ELEVENLABS_API_KEY`, `MESSAGING_PROVIDER=meta` and the `WHATSAPP_*` values,
   `PUBLIC_BASE_URL=https://<your tunnel>`.
3. `docker compose --profile tunnel up -d` (quick tunnel) and check providers:
   `curl localhost:8100/api/health/providers` (shows AI/TTS/messaging status and the pipeline config).
4. Send clips from your verified phone to the test number.

Tip for testers: send clips **as a document** (📎 → Document) to avoid WhatsApp's compression.

## 3. Tests
```bash
# full suite incl. real FFmpeg rendering (inside the image)
docker compose exec postgres createdb -U reelchat reelchat_test
docker compose run --rm --no-deps -e TASK_BACKEND=inline -e TEST_DATABASE_URL=postgresql+psycopg://reelchat:reelchat@postgres:5432/reelchat_test api pytest
# fast local run (FFmpeg tests skip if ffmpeg is missing)
cd backend && uv venv && uv pip install -e ".[dev]" && .venv/bin/pytest
# Requires TEST_DATABASE_URL pointing to a dedicated PostgreSQL database ending in _test.
```
Suites: `test_units.py` (extraction, safety, playbooks, sequencing, captions, plugin/HTTP steps, Gemini adapter),
`test_flow.py` (acceptance flow: anonymous draft, batch timeout, revision, linking/merge, duplicate confirmation,
injection, failures/retries/refunds, quotas, 24 h window, tenant isolation), `test_webhook_auth.py` (signatures,
idempotency, CSRF, link codes, signed URLs, confined file links, account deletion), `test_media.py` (real FFmpeg:
intake validation, quality filter, render, compression, end-to-end with sample footage).

Postgres check for migrations: `docker compose run --rm api alembic upgrade head && docker compose run --rm api alembic check`.

## 4. Production deployment (single server)
* A VM with 2+ vCPU / 4 GB RAM per worker; Docker Compose; a domain with a **named Cloudflare Tunnel** or a
  reverse proxy with TLS (Caddy/Nginx) in front of `api` and `web`.
* `.env`: `APP_ENV=production`, strong `SECRET_KEY`, real `WHATSAPP_APP_SECRET`/`WHATSAPP_VERIFY_TOKEN` (the API
  refuses to start with defaults), `COOKIE_SECURE=true`, `PUBLIC_BASE_URL`/`WEB_BASE_URL` with https,
  `STORAGE_BACKEND=s3` with a **private** bucket (build with `--build-arg EXTRAS=dev,s3`), `EMAIL_PROVIDER=smtp`.
* Managed Postgres + Redis recommended; back up Postgres daily.
* Scale by running more `worker` containers (`docker compose up -d --scale worker=3`); keep exactly one with `-B`
  (beat) — split beat into its own service if you scale.
* Before launch: business verification, a real number on the WABA, an approved `reel_ready` utility template,
  re-check Meta's Business Solution Terms (AI chatbot policy), the ElevenLabs plan limits, and the music licences.
* Observability: JSON logs to stdout (phones/emails masked, message bodies never logged); `/api/health`;
  per-job `pipeline`, `stage`, `error_code` in the DB.
