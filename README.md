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

## AgenticOrg message interpretation

To interpret incoming text and voice transcripts using an existing AgenticOrg agent,
set these values in `.env`:

```dotenv
AGENT_PROVIDER=agenticorg
AGENTICORG_API_KEY=your-key
AGENTICORG_AGENT_NAME=your-agent-uuid-or-type
AGENTICORG_COMPANY_ID=your-company-uuid
```

`AGENTICORG_COMPANY_ID` is required for agent types; it is optional for agent UUIDs.
Optionally set `AGENTICORG_BASE_URL` for your deployment and `AGENTICORG_ACTION`
(default `process`). Requests use `AGENT_TIMEOUT_SECONDS` (default 30).
The Docker build includes the SDK; rebuild with `docker compose up --build`.
For a local backend, install with `pip install -e ".[agenticorg,dev]"` from `backend/`.
If Admin has a saved agent override, select `agenticorg` there or reset it to environment config.

The agent receives `inputs.statement` and `inputs.context` and must return a
`MessageAnalysis` JSON object in `result.output`. That interpretation passes directly
to requirement collection and validation. Video generation still requires user confirmation.
Script writing uses the existing rules implementation. Failed calls or invalid JSON use
the existing rules fallback.

See [the input/output contract and agent configuration guidance](docs/AGENTICORG.md).

## Tests

```bash
docker compose run --rm --no-deps api pytest            # full suite incl. FFmpeg rendering (SQLite)
cd backend && uv venv && uv pip install -e ".[all,dev]" && .venv/bin/pytest   # local; FFmpeg tests skip if ffmpeg is missing
```

## Stack

FastAPI · SQLAlchemy 2 · Alembic · PostgreSQL (also the task queue) · FFmpeg · espeak-ng · Next.js 15 ·
Docker Compose. AI providers: Anthropic Claude (initial), OpenAI / OpenAI-compatible (Ollama, vLLM), Gemini,
plus an offline rules agent. WhatsApp Business Platform (Cloud API). S3-compatible storage.
