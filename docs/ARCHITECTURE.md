# Architecture

ReelChat is a **modular monolith**: one Python codebase (FastAPI) deployed as two process types — `api` and
`worker` — plus a Next.js website, PostgreSQL and object storage.

```
                         ┌───────────────────────── Next.js website (web) ─────────────────────────┐
  Browser ──HTTPS──────▶ │ landing · register/login · dashboard · project history · admin · dev sim │
                         └──────────────┬───────────────────────────────────────────────────────────┘
                                        │ same-origin /api/* (rewrite proxy, session cookie + CSRF)
  WhatsApp user                         ▼
      │            ┌──────────────────────────────── FastAPI (api) ──────────────────────────────────┐
      │  Meta      │ /api/auth  /api/me  /api/projects  /api/files (signed)  /api/admin  /api/dev     │
      └─Cloud API─▶│ /api/webhooks/whatsapp  ── verify X-Hub-Signature-256 → store message (unique    │
        webhook    │                            wamid) → enqueue process_inbound → 200 OK             │
                   └───────────────┬─────────────────────────────────────────────────────────────────┘
                                   │ INSERT task in same transaction
                                   ▼
                   ┌──────────── PostgreSQL ─────────────┐
                   │ users · sessions · whatsapp_accounts │
                   │ conversations · messages             │
                   │ video_projects · video_preferences   │
                   │ generation_jobs · generated_assets   │
                   │ provider_configurations              │
                   │ usage_records · audit_logs           │
                   │ task_queue  (SKIP LOCKED claims)     │
                   └──────────────┬───────────────────────┘
                                  │ claim
                                  ▼
  ┌──────────────────────────────── worker (python -m app.jobs.worker) ───────────────────────────────┐
  │ process_inbound ─▶ ConversationEngine (state machine)                                             │
  │                     ├─ TranscriptionProvider (voice notes)      mock | openai                     │
  │                     ├─ AgentProvider.analyze_message()          rules | anthropic | openai |      │
  │                     │    (intent + proposed fields → validated)   openai_compatible | gemini      │
  │                     └─ tools: start_generation · cancel · retry · resend_link   (ownership,       │
  │                          state, nonce, credits, idempotency checked in code)                     │
  │ generate_video ─▶ VideoProvider.render()                         mock | stock (Pexels)            │
  │                     script → scenes → visuals → TTS voiceover → SRT captions → FFmpeg assembly    │
  │                     → validate (9:16, duration, h264, audio) → ObjectStorage (local | S3)         │
  │ deliver        ─▶ MessagingProvider (outbox, ordered)            dev | meta                       │
  │                     video upload ≤16 MB, else signed expiring link; template outside 24 h window  │
  │ purge_expired  ─▶ retention: old messages, expired assets, finished tasks                         │
  └────────────────────────────────────────────────────────────────────────────────────────────────────┘
```

## Key design decisions

| Decision | Why |
|---|---|
| **Modular monolith, 2 process types** | Smallest thing that scales: run more `worker` replicas for throughput. Modules (`agents`, `messaging`, `video`, `tts`, `transcription`, `storage`) are boundaries that can be split out later. |
| **Postgres task queue instead of Redis + Celery** (documented substitution) | Tasks are inserted in the *same transaction* as the change that caused them (the job row and its `generate_video` task; a reply and its `deliver` task). No dual-write, so jobs can't be lost or orphaned. `FOR UPDATE SKIP LOCKED` lets several workers run safely; `dedupe_key` makes enqueueing idempotent; retries use exponential backoff; exhausted tasks become `dead` (dead-letter) and trigger a cleanup hook. One less service to operate. Swap for Celery/RQ behind `app/jobs/queue.py` if throughput demands it. |
| **The agent proposes, the state machine decides** | `AgentProvider.analyze_message()` returns JSON (intent + field proposals) that is validated against closed option lists in `conversation/requirements.py`. The model has no tools and can't act. Every side effect is a method on `ConversationEngine` that re-checks ownership, state, credits and idempotency. |
| **Deterministic confirmation gate** | Spending credits requires either a button reply carrying the summary's current *nonce* or a short, explicit "yes" matched by a regex (`conversation/safety.py`). A model saying `intent=confirm` is not enough — this defeats prompt injection in messages and transcripts. Changing any setting rotates the nonce. |
| **One paid job per approved revision** | `generation_jobs.idempotency_key = "<project>:r<revision>"` is UNIQUE. Double taps, webhook redeliveries and races all collapse to one job. Retry re-runs the *same* job (`run_number+1`) with idempotent credit reserve/refund ledger keys. |
| **Credits reserved at confirmation, refunded on failure** | Cost control: a user can't start work they can't pay for; failed/cancelled jobs are refunded automatically (ledger in `usage_records`). Daily cap and one concurrent job per user. |
| **Transactional outbox for WhatsApp** | Replies are rows (`messages`, status `pending`) + one `deliver` task per turn, delivered in order; transient Meta errors retry without losing order. Delivery status webhooks update rows. |
| **24-hour window aware** | Free-form messages only inside the customer-service window; otherwise the configured approved template (`WHATSAPP_NOTIFY_TEMPLATE`). The dev provider enforces the same rule. |
| **Same webhook format in dev** | The dev simulator builds Cloud-API-shaped payloads and signs them with the app secret, so signature checks, parsing and idempotency are exercised end to end without Meta credentials. |
| **Provider registry, env + admin override** | `app/providers.py` resolves `provider_configurations` (admin UI) → environment. Secrets only in env. Agent models can be set per task (`analysis`, `response`, `script`). An LLM outage falls back to the `rules` agent instead of breaking the chat. |
| **FFmpeg for assembly** | Every provider shares script → scenes → TTS → captions → assembly → validation in `video/base.py`; providers differ only in `generate_video()` (visuals). A generative video API plugs in there. |
| **Mock output is labelled** | The mock video provider burns *"DEV MOCK PREVIEW – NOT AI-GENERATED"* into every frame, assets carry `is_mock`, and the WhatsApp delivery is preceded by a mock notice. |

## Conversation state machine

```
(none) ─create──▶ awaiting_input ──groups answered──▶ awaiting_confirmation ──yes/button+nonce──▶ queued
                    │  ▲ asks ≤3 grouped questions          │  ▲ edits rotate nonce              │ cancel → refund
                    │  └ topic · look · format              │  └────────────────────            ▼
                    └─ cancel ─▶ cancelled                   └─ cancel ─▶ cancelled           processing
                                                                                                │      │
                                                                                           completed  failed (refund)
                                                                     modify ─▶ revision n+1 ──┘      │ retry (same job)
                                                                              awaiting_confirmation   ▼
                                                                                                    queued
```

Question groups (`QUESTION_GROUPS`): **topic** (required), **look** (vibe + visual style), **format** (format,
length, quality). A group is skipped if the user already gave all its fields, and each group is asked at most once
— "you choose" accepts defaults. Values carry a source (`user` / `default` / `inferred`); defaults never
overwrite user values, and the summary marks suggestions.

## Code map

```
backend/app
├── main.py                 FastAPI app, security headers, admin bootstrap
├── config.py               all settings (env vars)
├── models.py               SQLAlchemy schema
├── security.py             argon2, HMAC-signed tokens/URLs, webhook signatures, phone normalisation
├── providers.py            provider registry + health
├── api/                    auth · me · projects/files · webhooks · admin · dev simulator
├── agents/                 base (AgentProvider) · rules · llm (Anthropic, OpenAI/compatible, Gemini)
├── conversation/           engine (state machine + tools) · requirements · replies · safety
├── messaging/              base (interface + webhook parser) · meta_cloud · dev
├── transcription/          mock (fixtures) · openai
├── tts/                    mock · espeak · openai
├── video/                  base (VideoProvider pipeline) · mock · stock · ffmpeg helpers
├── storage/                local · S3
├── jobs/                   queue · tasks · worker
└── services/               credits ledger · outbox · audit · email
frontend/app                Next.js App Router pages (see docs/SETUP.md)
```
