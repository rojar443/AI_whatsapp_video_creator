# Status: implemented · demo stand-ins · production prerequisites

## Implemented and tested
| Area | What | Verified by |
|---|---|---|
| WhatsApp intake | signed webhook, idempotent by `wamid`, text / voice / image / video / document / buttons, statuses | `test_webhook_auth.py` |
| Batching | per-file storage, debounced ack, "that's all" or 90 s timeout, late media reopens the batch, 20-file cap | `test_flow.py` |
| Media security | type + magic bytes + ffprobe (codec, duration, pixels), size caps while streaming, generated storage keys, sandboxed FFmpeg (rlimits, timeouts, `-protocol_whitelist file,pipe`), capability-dropped worker | `test_media.py` (HLS-disguised file rejected) |
| Anonymous first draft | anonymous profile, 2 credits / 2 projects / 3-day retention, register nudge, merge on `LINK` | `test_flow.py` |
| Conversation | rules + Gemini analysis, genre question with buttons, defaults from playbooks, revisions, off-topic decline, voice notes | `test_flow.py`, `test_units.py` |
| Confirmation gate | plan nonce buttons or explicit yes; injection-style messages never start a job; stale buttons rejected | `test_flow.py` |
| Playbooks | travel + daily-life v1 YAML, frozen schema, versions recorded per plan, DB sync with checksum | `test_units.py` |
| Pipeline slots | builtin / `python:` plugin / HTTP agent per step, typed contracts, signed file URLs, retries, builtin fallback | `test_units.py` |
| Selection | quality filter (dark/shaky/duplicate), scene split, Gemini vision scoring + heuristic fallback, hook/build/payoff sequencing fitted to length | `test_units.py`, `test_media.py` |
| Narration & captions | Gemini script (template fallback), ElevenLabs with-timestamps → word timings → ASS captions | `test_units.py` (fake HTTP for ElevenLabs alignment) |
| Rendering | 9:16 crop around subject, trims, photo slow zoom/pan, grade per style, fade/cut transitions, title, burned captions, narration + ducked music + original audio, bitrate from duration, re-encode until ≤ target, validation (dims, ratio, duration, H.264/AAC) | `test_media.py` (real FFmpeg in Docker) |
| Delivery | outbox in order, video ≤16 MB else signed link, attachment-rejected → link, retries, 24 h window: hold + template + release on reply | `test_flow.py` |
| Jobs | Celery + Redis, enqueue-after-commit, idempotent tasks, retries with backoff, final-failure hooks, sweeper, retention purge | `test_flow.py` (inline backend); Celery path smoke-tested against Redis + Postgres |
| Credits | reserve on confirm, refund on failure/cancel, one job per revision (UNIQUE), retry = same job, ledger | `test_flow.py` |
| Web | register/login/verify, link WhatsApp, reels history with plan + uploads + jobs, download, usage, unlink, delete account, privacy/terms, dev simulator | `next build`; API tests |
| Data | 14 tables incl. `project_assets`, `genre_playbooks`, `voice_profiles`; Alembic migration verified on Postgres (`alembic check` clean) | |

| Telegram channel | Bot API long polling (text, photo, video, files, voice, buttons), `tg:<chat>` ids through the same engine, deep-link account linking, 20 MB download limit handled | `test_telegram.py` (fake Bot API) |

## Demo stand-ins (used when keys are missing — always labelled)
| Stand-in | Real integration | Needs |
|---|---|---|
| `rules` agent (keywords) | Gemini (`AI_PROVIDER=gemini`) | `GEMINI_API_KEY` |
| heuristic moment scoring | Gemini vision | same key |
| template script | Gemini script | same key |
| `mock` transcription (simulator hint) | Gemini audio transcription | same key |
| `mock` (silent) / `espeak` voice | ElevenLabs with timestamps | `ELEVENLABS_API_KEY`, voice id |
| generated ambient music bed | licensed tracks in `backend/music/library.yaml` | tracks + licences |
| `dev` messaging + simulator | Meta Cloud API (`MESSAGING_PROVIDER=meta`) | Meta app, token, phone number id, app secret, webhook via tunnel — [WHATSAPP_SETUP.md](WHATSAPP_SETUP.md) |
| synthetic sample footage | real phone clips | a real phone |

Reels made with stand-ins carry `is_demo` and a "Demo mode" WhatsApp notice. Nothing reports success it didn't achieve.

## Test results
88 tests pass in the Docker image with real FFmpeg (SQLite) and 81 on Postgres (FFmpeg tests skip where it's absent). The Celery + Redis + Postgres path was smoke-tested end to end with the simulator. The website builds (`next build`) and all pages respond through the API proxy.

## Not yet verified against live services
The Gemini, ElevenLabs and Meta adapters are written against their documented APIs and covered with fake
clients, but have **not** been exercised with real keys in this environment. First live run checklist:
1. `GET /api/health/providers` shows `ai.ok` and `messaging.ok`.
2. Gemini model id: `gemini-flash-latest` is an alias — pin a specific model in `AI_MODEL` once chosen.
3. ElevenLabs: confirm the voice id exists on your account and the model returns `alignment`.
4. Send 3–6 real clips from a verified phone; check the plan, then the reel size/quality on the phone.

## Outstanding / phase 2
* Voice cloning with consent flow (table + privacy text exist; flow not built — spec: after default voice works).
* Transcription-based captions when the user keeps original speech without narration (currently: script-based only).
* Playbook research and v2 (see [PLAYBOOK_RESEARCH.md](PLAYBOOK_RESEARCH.md)); A/B preference test with users.
* Languages other than English.
* Rate limiting is per-process (move to Redis for multiple API replicas); no billing/payments; no admin UI (by spec).

## Production prerequisites
Business verification and a real number on the WABA · approved `reel_ready` utility template · recheck Meta
Business Solution Terms (AI chatbot policy) · ElevenLabs plan allowing your volume (and cloning limits for phase
2) · licensed music · private S3 bucket · TLS domain + named tunnel or reverse proxy · strong secrets
(`APP_ENV=production` refuses defaults) · managed Postgres/Redis with backups · legal review of privacy/terms.
