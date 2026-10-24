# Design

## Context

- The code is a WhatsApp-first MVP: `messaging/` (Meta Cloud + dev simulator), a conversation engine keyed by `WhatsAppAccount.wa_id`, interactive button ids like `confirm:<project>:<nonce>`, a Postgres task queue, and a `VideoProvider` pipeline (script → scenes → stock/mock visuals → TTS → captions → FFmpeg assembly).
- `add-dual-login` (uncommitted, branch `ui-web`) added phone-based WhatsApp login, `login_challenges`, nullable email/password and re-auth. It is **superseded**; its poll-secret handoff, single-use redemption and nullable-credential ideas carry over, its phone logic does not. It must not be archived.
- Nothing is deployed, so there is no data to migrate; the schema can be re-baselined.
- Constraints: app must run on CPU-only PCs (no GPU models); FFmpeg is already the rendering engine; Telegram Bot API limits are 20 MB per bot download (`getFile`) and 50 MB per bot upload (`sendVideo`).

See proposal.md for motivation.

## Goals / Non-Goals

**Goals:**
- Same chat-driven product on Telegram, with the user's own clips as the picture source.
- Login with nothing but Telegram; no passwords.
- Reuse the queue, credits ledger, confirmation-nonce safety and FFmpeg tooling.

**Non-Goals:**
- Running WhatsApp and Telegram side by side; group chats; Telegram Mini Apps.
- AI/vision models, beat-synced editing, user-supplied music, generative video.
- Email receipts, payments, top-ups.
- Self-hosted Telegram Bot API server (large files go through the website).

## Decisions

**1. Telegram adapter behind the existing `MessagingProvider`.** Implement `TelegramProvider` (send text/buttons/video, download file) with the Bot API over `httpx`; delete `meta_cloud.py`. Webhook route `POST /api/webhooks/telegram` checks `X-Telegram-Bot-Api-Secret-Token`; `update_id` is the idempotency key (replacing `wamid`). For local runs a `TELEGRAM_MODE=polling` worker loop calls `getUpdates` and feeds the same parser, so no public URL or tunnel is needed. The dev simulator posts Telegram-shaped updates (signed with the secret header) like it does today for WhatsApp. *Alternative:* a Telegram library (python-telegram-bot) — rejected, a thin client keeps the dependency list short and the parser testable.

**2. Identity and tables.** `telegram_accounts(tg_id BIGINT unique, username, first_name, language, user_id unique, blocked_at, …)` replaces `whatsapp_accounts`; `conversations` hang off it. `users` drops `email`, `password_hash`, `phone_e164`, `email_verified_at` (no passwords, nothing to verify). `chat_id` equals `tg_id` in private chats, so one column serves both. *Alternative:* keep email as optional — deferred; not needed until receipts exist.

**3. Login tokens.** `login_tokens(id, token_hash, poll_secret_hash, status pending|confirmed|consumed|denied, user_id, expires_at, …)` — the `login_challenges` idea without the phone. The token is 32 url-safe chars (fits Telegram's 64-char `start` payload) and is stored hashed. Flow: `POST /api/auth/telegram/start` → deep link + HttpOnly `rc_login` cookie; bot handles `/start <token>`; `GET /api/auth/telegram/status` redeems once with an atomic `UPDATE … WHERE status='confirmed'`. A bare `/start` creates the account and the website later finds it by the same Telegram ID. *Alternative:* Telegram Login Widget — needs a registered HTTPS domain and does not work on localhost; can be added later on top of the same account model.

**4. Short button tokens.** Telegram `callback_data` is limited to 64 bytes, but `confirm:<uuid>:<nonce>` is ~75. Introduce `button_tokens(token 12 chars, user_id, action, project_id, nonce, expires_at)`; the engine validates ownership, state and the current nonce exactly as today, so the deterministic confirmation gate is unchanged.

**5. Clips.** `user_clips(id, user_id, project_id, storage_key, original_name, size_bytes, duration, width, height, rotation, position, source chat|web, created_at, deleted_at)` using the existing `storage` abstraction. Intake validates with `ffprobe` (decodable video stream, duration and size caps from settings: default 500 MB/clip, 10 clips, 2 GB per project). Chat intake downloads via `getFile` (≤20 MB); over that, the bot replies with a signed upload URL (`sign_payload({pid, uid}, "clip-upload", 1h)`) to `/upload?t=…`, which accepts streamed multipart uploads for that project only and needs no session. Owner downloads use the existing signed-file mechanism. A retention task (extends `purge_expired`) deletes clips N days after delivery.

**6. Editing pipeline (CPU only, no AI vision).** New `ClipEditProvider` replacing stock/mock as the default video provider:
1. *Analyse* each clip once on a small proxy (e.g. 360p): `ffprobe` metadata, FFmpeg scene-change detection to find shot boundaries, and cheap per-segment quality scores (sharpness via `blurdetect`/Laplacian variance, exposure via `signalstats`, motion via frame difference).
2. *Select*: split into candidate segments of the pace length (reuse `SCENE_SECONDS`: slow 7.5 / normal 6 / fast 4 s), drop the worst-scoring, then pick across clips (round-robin by upload order) until the target length is reached; keep chronological order unless asked otherwise.
3. *Render* at full quality: `fit_clip` each segment to the vertical frame (scale+crop or blurred-background pad for landscape, honouring rotation), join with short crossfades, mix music from the existing `synth_music` under the original audio, burn optional captions/title via the existing assembly step, validate with `validate_mp4`.
The LLM remains only for chat and the brief; it never sees video. *Alternative:* vision-model shot ranking — rejected for CPU-only/cost, and it can be slotted into step 2 later.

**7. Brief schema.** Reuse `requirements.py`, dropping script/voice/TTS fields from the question flow (narration is out of scope) and adding `clips_mode` defaults; credits = `credits_for(duration, quality)` as now. The plan message lists clip names/durations.

**8. Re-baseline migrations.** Replace the uncommitted `0001`/`0002` with one new baseline revision generated from the final models; no data migration.

**9. Website design system.** The site moves to a light, playful visual language ("light and funky": bright off-white background, bold saturated accents, chunky outlines with offset shadows, rounded shapes, sticker-style badges, friendly display font). Tokens live in `globals.css` custom properties; dark mode is dropped for now to keep one polished look. Landing, login, dashboard, project and upload pages use it.

## Risks / Trade-offs

- [20 MB chat limit makes phone clips hard to send in chat] → Website upload link from the bot; the landing and bot copy lead with it.
- [FFmpeg analysis/render is slow on CPU] → Analyse on proxies once and cache results per clip; cap total input minutes per project; keep one concurrent job per user; progress messages already exist.
- [Source clips are personal media (faces, locations)] → Private storage, owner-only signed access, retention + delete-on-account-delete, terms/privacy text updated.
- [Deep-link token phishing: a victim presses Start on an attacker's link, signing the attacker's browser in as the victim] → Bot reply names the action ("You're signing in to ReelChat on a browser — only continue if you started this"), short TTL, and a confirm button in the bot before the session is released.
- [User blocks the bot] → Mark `blocked_at`, stop retries, show a website notice; videos stay downloadable there.
- [Quality of automatic "best moments" is only heuristic] → Be honest in the plan, support revisions ("more of the beach", "faster"), and keep selection pluggable for a later model.
- [Removing passwords removes any recovery path] → Telegram account loss equals account loss; accepted for MVP, revisit with optional email.

## Migration Plan

1. Land the spec; implement behind the new baseline migration on a fresh database (nothing to migrate).
2. Create the bot in BotFather, set `TELEGRAM_BOT_TOKEN`, `TELEGRAM_BOT_USERNAME`, `TELEGRAM_WEBHOOK_SECRET`; set the webhook (or run polling locally).
3. Remove WhatsApp config and docs. Rollback: revert the change; no data to restore.

## Open Questions

- Default retention for source clips (proposed 14 days after delivery) and the per-project limits above can be tuned after first use.
- Whether to add the Telegram Login Widget as a second web entry point once a production domain exists.
