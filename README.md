# ReelChat — WhatsApp-first AI short-video creator (MVP)

Users register on the website, link their WhatsApp number, then **describe a video by text or voice note**.
An AI agent asks only for what's missing, shows a summary, and — after an explicit "yes" — generates a vertical
MP4 asynchronously and sends it back in the same chat. Users can request revisions and see their history on the
dashboard. Admins can switch the AI agent and video providers without code changes.

* **Architecture & design decisions:** [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)
* **Setup, running, tests, deployment:** [docs/SETUP.md](docs/SETUP.md)
* **API reference:** [docs/API.md](docs/API.md) (live: `/api/docs`)
* **Providers & cost assumptions:** [docs/PROVIDERS_AND_COSTS.md](docs/PROVIDERS_AND_COSTS.md)
* **Example WhatsApp conversations:** [docs/CONVERSATIONS.md](docs/CONVERSATIONS.md)
* **Implemented vs mocked vs production prerequisites:** [docs/STATUS.md](docs/STATUS.md)

## Quick start (Docker)

```bash
cp .env.example .env            # defaults run fully offline: rules agent, mock video, dev WhatsApp
docker compose up --build       # web :3100 · api :8100 · postgres :55433 (all bound to 127.0.0.1)
```

1. Open http://localhost:3100/register, create an account (dev mode shows the email-verification link).
2. Dashboard → **Get link code**.
3. Open **WhatsApp simulator** (top nav), send `LINK <code>`.
4. Send *"Create a motivational Reel about fitness"*, answer the question, tap **✅ Generate**.
5. Watch the progress messages; the (watermarked mock) video arrives in the chat and on the dashboard.
6. Send *"Make it more cinematic and use a faster pace"* → confirm → revision 2.

Admin: set `ADMIN_EMAIL` / `ADMIN_PASSWORD` in `.env`, log in, open **Admin**.

## Tests

```bash
docker compose run --rm --no-deps api pytest            # full suite incl. FFmpeg rendering (SQLite)
cd backend && uv venv && uv pip install -e ".[all,dev]" && .venv/bin/pytest   # local; FFmpeg tests skip if ffmpeg is missing
```

## Stack

FastAPI · SQLAlchemy 2 · Alembic · PostgreSQL (also the task queue) · FFmpeg · espeak-ng · Next.js 15 ·
Docker Compose. AI providers: Anthropic Claude (initial), OpenAI / OpenAI-compatible (Ollama, vLLM), Gemini,
plus an offline rules agent. WhatsApp Business Platform (Cloud API). S3-compatible storage.
