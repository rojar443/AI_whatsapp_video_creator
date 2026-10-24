# API reference

Live OpenAPI: **http://localhost:8100/api/docs**. JSON in/out; errors `{"detail": "..."}`.
Auth: `rc_session` HttpOnly cookie; every non-GET request needs `X-CSRF-Token` = `rc_csrf` cookie.

## Auth & account
| Method | Path | Notes |
|---|---|---|
| POST | `/api/auth/register` | `{name, email, password(≥10), phone, accept_terms}` → 201 + session; dev returns `dev_verification_url` |
| POST | `/api/auth/login` · `/api/auth/logout` | 10 logins / 5 min / IP |
| POST | `/api/auth/verify-email` | `{token}` (HMAC-signed, 48 h) |
| GET | `/api/me` | profile, credits, `whatsapp.status` linked/pending/not_linked |
| GET | `/api/me/usage` | credit ledger |
| POST | `/api/me/whatsapp/link` | → `{code, message: "LINK 123456", wa_me_url}`; send it **from the registered number**; merges anonymous drafts |
| DELETE | `/api/me/whatsapp` | unlink |
| POST | `/api/me/telegram/link` | → `{url: https://t.me/<bot>?start=link_<signed token>}`; opening it in Telegram links that chat (15 min) |
| DELETE | `/api/me/telegram` | unlink Telegram |
| POST | `/api/me/delete` | `{password}` — deletes account, chats, projects, files |

## Projects & files
| Method | Path | Notes |
|---|---|---|
| GET | `/api/projects` | own projects, newest first, latest reel with signed URL |
| GET | `/api/projects/{id}` | preferences + plan per revision (moments, reasons, skipped files, script), uploads, jobs (+ pipeline used), reels |
| POST | `/api/projects/{id}/retry` | retry failed render of the current revision (no double charge) |
| GET | `/api/assets/{id}/link` | fresh signed download URL |
| GET | `/api/files/{id}?exp&sig[&inline=1]` | signed, expiring download (no session needed); 410 expired, 403 tampered |
| GET | `/api/pipeline-files/{token}` | signed 1-hour links to work files for HTTP pipeline agents (confined to work/storage/music dirs) |

## WhatsApp webhook
| Method | Path | Notes |
|---|---|---|
| GET | `/api/webhooks/whatsapp` | `hub.mode=subscribe&hub.verify_token=…&hub.challenge=…` |
| POST | `/api/webhooks/whatsapp` | requires valid `X-Hub-Signature-256`; idempotent by `wamid`; text, audio, image, video, document (video/image), interactive, template buttons; delivery statuses |

Button ids sent by the bot: `confirm:<project>:<nonce>` · `edit:<project>` · `cancel:<project>` ·
`genre:<project>:travel|daily_life` · `retry:<job>` — each is checked against the sender's ownership and state.
Text commands: `help`, `status`, `cancel`, `retry`, `link`, `new`, `register`, `that's all`, `LINK 123456`.

## Health & dev
| Method | Path | Notes |
|---|---|---|
| GET | `/api/health` | DB check |
| GET | `/api/health/providers` | dev only: AI, TTS, transcription, messaging, ffmpeg, pipeline steps |
| GET | `/api/config` | public config |
| GET | `/api/dev/info` | dev: providers and pipeline |
| POST | `/api/dev/whatsapp/send` · `/media` · `/samples` · `/voice` | dev simulator (as logged-in user's number, or `phone` for anonymous tests) |
| GET | `/api/dev/whatsapp/messages` | dev: the chat |
| GET | `/api/dev/emails` | dev: captured emails |

Pipeline agent contract: [PIPELINE_AGENTS.md](PIPELINE_AGENTS.md).
