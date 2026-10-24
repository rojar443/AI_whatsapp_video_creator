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
sed -i 's/^AI_PROVIDER=.*/AI_PROVIDER=rules/; s/^TTS_PROVIDER=.*/TTS_PROVIDER=espeak/; s/^TRANSCRIPTION_PROVIDER=.*/TRANSCRIPTION_PROVIDER=mock/' .env
docker compose up --build -d
```
Open http://localhost:3100/dev/whatsapp → 📎 → **Send sample footage** → *"That's all. Make it a cinematic
travel reel with a calm voiceover."* → **✅ Proceed**. For the real thing, follow
[docs/WHATSAPP_SETUP.md](docs/WHATSAPP_SETUP.md) and set the Gemini / ElevenLabs keys.

## Stack
FastAPI · SQLAlchemy/Alembic · PostgreSQL · Redis + Celery · FFmpeg · Next.js 15 · Docker Compose ·
Cloudflare Tunnel. AI: Google Gemini (initial), adapters for OpenAI/compatible (Ollama, vLLM) and Anthropic, plus
an offline rules agent. Voice: ElevenLabs (espeak/mock offline). Messaging: WhatsApp Cloud API (+ dev simulator).
