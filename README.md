# ReelChat — AI WhatsApp reel editor (MVP)

Users send the raw clips and photos on their phone to a WhatsApp number, describe the vibe by text or voice
note, approve a plan that shows which moments were picked and why, and get a finished 9:16 reel back in the
same chat. No app, no editing skills, no signup needed for the first draft.

**Pipeline:** quality filter (dark/shaky/duplicate) → scene split → moment scoring (Gemini vision vs. the genre
playbook) → hook/build/payoff sequencing → AI narration script → ElevenLabs voice → captions from the script →
licensed music → FFmpeg render (crop, trim, photo motion, grade, transitions, title, ducked mix) → compression
to fit WhatsApp's 16 MB → validation → delivery. **Every step is a configurable slot** for your own agents
(Python plugin or HTTP service) — see [docs/PIPELINE_AGENTS.md](docs/PIPELINE_AGENTS.md).

| Doc | |
|---|---|
| [docs/PRD.md](docs/PRD.md) | short product requirements, limits, risks, external services |
| [docs/WHATSAPP_SETUP.md](docs/WHATSAPP_SETUP.md) | connect a real WhatsApp number (Meta app, token, webhook, Cloudflare Tunnel) |
| [docs/TELEGRAM_SETUP.md](docs/TELEGRAM_SETUP.md) | use a Telegram bot as the input channel (works on localhost) |
| [docs/SETUP.md](docs/SETUP.md) | run locally, tests, production deployment |
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | design decisions, state machine, ER diagram, code map |
| [docs/PIPELINE_AGENTS.md](docs/PIPELINE_AGENTS.md) | plug in your own processing agents |
| [docs/API.md](docs/API.md) | HTTP API (live at `/api/docs`) |
| [docs/CONVERSATIONS.md](docs/CONVERSATIONS.md) | example WhatsApp conversations |
| [docs/PLAYBOOK_RESEARCH.md](docs/PLAYBOOK_RESEARCH.md) | genre playbook research plan |
| [docs/STATUS.md](docs/STATUS.md) | implemented vs demo stand-ins vs production prerequisites |

## Quick start (offline demo)
```bash
cp .env.example .env
sed -i 's/^AI_PROVIDER=.*/AI_PROVIDER=rules/; s/^TTS_PROVIDER=.*/TTS_PROVIDER=mock/; s/^TRANSCRIPTION_PROVIDER=.*/TRANSCRIPTION_PROVIDER=mock/' .env
docker compose up --build -d
```

1. Open http://localhost:3100/register, create an account (dev mode shows the email-verification link).
2. Dashboard → **Get link code**.
3. Open **WhatsApp simulator** (top nav), send `LINK <code>`.
4. Send *"Create a motivational Reel about fitness"*, answer the question, tap **✅ Generate**.
5. Watch the progress messages; the (watermarked mock) video arrives in the chat and on the dashboard.
6. Send *"Make it more cinematic and use a faster pace"* → confirm → revision 2.

Admin: set `ADMIN_EMAIL` / `ADMIN_PASSWORD` in `.env`, log in, open **Admin**.

For a no-account demo, open http://localhost:3100/dev/whatsapp → 📎 → **Send sample footage** → *"That's all. Make it a cinematic
travel reel with a calm voiceover."* → **✅ Proceed**. For real WhatsApp, follow
[docs/WHATSAPP_SETUP.md](docs/WHATSAPP_SETUP.md) and set the Gemini / ElevenLabs keys.

## AgenticOrg message interpretation

To interpret incoming text and voice transcripts using an existing AgenticOrg agent,
set these values in `.env`:

```dotenv
AGENT_PROVIDER=agenticorg
AGENTICORG_API_KEY=your-key
AGENTICORG_AGENT_NAME=your-agent-uuid
AGENTICORG_BASE_URL=https://agenticorg.hackathon.pinelabs.com
```

`AGENTICORG_COMPANY_ID` is required for agent types; it is optional for agent UUIDs.
Optionally set `AGENTICORG_BASE_URL` for your deployment and `AGENTICORG_ACTION`
(default `process`). Requests use `AGENT_TIMEOUT_SECONDS` (default 30).
The Docker build includes the SDK; rebuild with `docker compose up --build`.
For a local backend, install with `pip install -e ".[agenticorg,dev]"` from `backend/`.
If Admin has a saved agent override, select `agenticorg` there or reset it to environment config.

The agent receives the user's text as `inputs.task`, with conversation details in
the SDK's separate `context` argument, and must return a
`MessageAnalysis` JSON object in `result.output`. That interpretation passes directly
to requirement collection and validation. Video generation still requires user confirmation.
Script writing uses the existing rules implementation. Failed calls or invalid JSON use
the existing rules fallback.

See [the input/output contract and agent configuration guidance](docs/AGENTICORG.md).

## Tests

```bash
docker compose exec postgres createdb -U reelchat reelchat_test
docker compose run --rm --no-deps -e TEST_DATABASE_URL=postgresql+psycopg://reelchat:reelchat@postgres:5432/reelchat_test api pytest
cd backend && uv venv && uv pip install -e ".[all,dev]" && .venv/bin/pytest   # local; FFmpeg tests skip if ffmpeg is missing
```

Native Windows startup: `powershell -File scripts/start-local.ps1`. PostgreSQL-only local setup and dedicated test database instructions are in [docs/SETUP.md](docs/SETUP.md#native-windows-setup-postgresql-only).

## Stack
FastAPI · SQLAlchemy/Alembic · PostgreSQL · Redis + Celery · FFmpeg · Next.js 15 · Docker Compose ·
Cloudflare Tunnel. AI: Google Gemini (initial), adapters for OpenAI/compatible (Ollama, vLLM) and Anthropic, plus
an offline rules agent. Voice: ElevenLabs (espeak/mock offline). Messaging: WhatsApp Cloud API and Telegram Bot API (+ dev simulator).
