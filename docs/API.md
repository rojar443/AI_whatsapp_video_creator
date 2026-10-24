# API reference

Interactive OpenAPI docs: **http://localhost:8100/api/docs** (`/api/openapi.json`).

Conventions
* JSON in/out. Errors: `{"detail": "..."}` with a meaningful status (401 unauthenticated, 403 forbidden/CSRF,
  404 not found *or not yours*, 409 conflict, 402 insufficient credits, 410 expired link, 422 validation, 429 rate limit).
* Auth: `rc_session` cookie (HttpOnly, SameSite=Lax, Secure in production). Every non-GET request must send
  header `X-CSRF-Token` equal to the `rc_csrf` cookie (also returned by register/login).
* The website calls the API same-origin via the Next.js `/api/*` rewrite.

## Auth

| Method | Path | Body | Notes |
|---|---|---|---|
| POST | `/api/auth/register` | `{name, email, password(≥10), phone, accept_terms}` | 201; starts a session; sends verification email. Dev mode also returns `dev_verification_url`. 5/hour/IP |
| POST | `/api/auth/login` | `{email, password}` | 10 per 5 min per IP |
| POST | `/api/auth/logout` | – | revokes the session |
| POST | `/api/auth/verify-email` | `{token}` | token is HMAC-signed, 48 h |
| POST | `/api/auth/resend-verification` | – | 3/hour |

## Account

| Method | Path | Notes |
|---|---|---|
| GET | `/api/me` | profile, credits, `whatsapp.status` = `linked` \| `pending` \| `not_linked` |
| GET | `/api/me/usage` | credit balance + ledger |
| POST | `/api/me/whatsapp/link` | requires verified email → `{code, message:"LINK 123456", wa_me_url, expires_in_minutes}`. The user sends the message **from their registered number**. 15 min, 5 attempts |
| DELETE | `/api/me/whatsapp` | unlink (stops all messages) |
| POST | `/api/me/delete` | `{password}` — deletes account, conversations, messages, projects, files |

## Projects & files

| Method | Path | Notes |
|---|---|---|
| GET | `/api/projects?limit&offset` | own projects, newest first, with latest video (signed URL, 1 h) |
| GET | `/api/projects/{id}` | preferences per revision (values + `sources` user/default/inferred), jobs, assets, `can_retry` |
| POST | `/api/projects/{id}/retry` | re-run the failed job of the current revision; 409 if running/nothing to retry, 402 if no credits |
| GET | `/api/assets/{id}/link` | fresh signed download URL (24 h) for an owned asset |
| GET | `/api/files/{id}?exp&sig[&inline=1]` | **no session needed** — HMAC-signed, expiring. 410 when expired, 403 when tampered. S3 backend → 302 to a presigned URL |

## WhatsApp webhook

| Method | Path | Notes |
|---|---|---|
| GET | `/api/webhooks/whatsapp?hub.mode=subscribe&hub.verify_token=…&hub.challenge=…` | Meta verification handshake |
| POST | `/api/webhooks/whatsapp` | Requires valid `X-Hub-Signature-256` (HMAC-SHA256 of raw body with `WHATSAPP_APP_SECRET`) else 401. Messages are stored idempotently by `wamid` and processed asynchronously; delivery statuses update outbound messages. Always 200 for valid signed bodies (so Meta doesn't redeliver) |

Supported inbound types: `text`, `audio` (voice notes), `interactive` (button/list replies), template `button`.
Anything else gets a polite "text or voice notes please".

### Button ids (sent by the agent)

| id | Action |
|---|---|
| `confirm:<project_id>:<nonce>` | start generation — only if the project is yours, awaiting confirmation and the nonce is current |
| `edit:<project_id>` | prompt for changes |
| `cancel:<project_id>` | cancel (refund if queued) |
| `retry:<job_id>` | retry a failed job without double charging |

### Text commands

`help` · `status` · `cancel` · `retry` · `link` (new download link) · `new` (start over) · `LINK 123456` (link account) — plus natural language.

## Admin (role `admin`)

| Method | Path | Notes |
|---|---|---|
| GET | `/api/admin/users?q` | users with credits, WhatsApp, job counts |
| PATCH | `/api/admin/users/{id}` | `{status?: active\|suspended, role?: user\|admin, credits_delta?}` — audited |
| GET | `/api/admin/providers` | available providers per category + active config (`source`: env or db) |
| PUT | `/api/admin/providers` | `{category: agent\|video\|tts\|transcription, provider, task?: default\|analysis\|response\|script, model?, reset?}` — agent providers are instantiated first to validate credentials; audited |
| GET | `/api/admin/health` | provider health checks + queue counts |
| GET | `/api/admin/usage?days` | usage/cost by kind and provider, job counts |
| GET | `/api/admin/jobs?status` | jobs + dead-letter tasks |
| POST | `/api/admin/jobs/{id}/retry` | retry a failed job — audited |
| GET | `/api/admin/audit-logs?action&limit` | audit trail |

## Dev only (`APP_ENV` = development/test; 404 in production)

| Method | Path | Notes |
|---|---|---|
| GET | `/api/dev/info` | active providers |
| POST | `/api/dev/whatsapp/send` | `{text}` or `{button_id, button_title}` — sent *as the logged-in user's number* through the signed webhook path |
| POST | `/api/dev/whatsapp/voice` | multipart `file` + `transcript` (hint for the mock transcriber) |
| GET | `/api/dev/whatsapp/messages` | the conversation as WhatsApp would show it |
| GET | `/api/dev/emails` | emails captured by the console email provider |

## Health

`GET /api/health` → `{ok, db, env}` · `GET /api/config` → public settings (app name, dev mode, business number).
