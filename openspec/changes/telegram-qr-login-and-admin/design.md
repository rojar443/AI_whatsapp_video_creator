# Design

## Context

State of `main` (merged into `ui-web`):
- Every WhatsApp or Telegram chat is a `WhatsAppAccount` row (Telegram ids are stored as `tg:<chat_id>`) pointing at a `User`; a chat with no website account gets an **anonymous** user (`is_anonymous=True`, `anon_credits`, shorter retention). Email login ignores anonymous users.
- `/api/me/telegram/link` already uses a signed payload and `/start link_<token>` (handled by `ConversationEngine._handle_telegram_link`) to attach a chat to an existing website account.
- Registration is controlled by `REGISTRATION_ENABLED` (off by default), so today a Telegram-only user has no way into the website.
- The old admin API and page were removed in the rewrite; there is no admin role bootstrap.
- Tasks run inline (`TASK_BACKEND=inline`) or on Celery; Telegram input arrives through `python -m app.telegram_poller` (long polling) and `/api/webhooks`.

See proposal.md for motivation.

## Goals / Non-Goals

**Goals:**
- Telegram (deep link + QR) as the website login, built on the existing `/start <payload>` mechanism.
- No loss of work: whatever an anonymous chat already made shows up after login.
- An admin tab listing every user who has used the app, with a few safe actions.

**Non-Goals:**
- Removing email login or WhatsApp; changing the reel pipeline.
- Telegram Login Widget (needs a registered HTTPS domain; can be added later onto the same session code).
- Showing conversation text or media to admins.

## Decisions

**1. `login_tokens` table (new migration `0002`).** `id`, `token_hash` (unique), `poll_secret_hash`, `status` (`pending|confirmed|consumed|denied`), `user_id`, `expires_at`, `created_at`. The 32-char url-safe token goes in the deep link as `/start login_<token>` (within Telegram's 64-char payload limit) and is stored hashed. *Alternative:* a stateless signed token like `link_` uses — rejected, a login must be confirmed by one party (bot) and redeemed by another (browser), once, which needs server state.

**2. Handoff with a poll secret.** `POST /api/auth/telegram/start` returns `{challenge_id, deep_link, expires_in_minutes}` and sets an HttpOnly `rc_login` cookie (secret; only its hash stored). `GET /api/auth/telegram/status?id=` returns `pending|expired`, or when confirmed and the cookie matches, creates the session through the existing `_start_session` and marks the token `consumed` with an atomic `UPDATE … WHERE status='confirmed'`; the cookie is then cleared. Rate limits per IP.

**3. Confirm in the engine.** Next to `/start link_…`, handle `/start login_…`: private Telegram chats only; look up the token by hash, check `pending` and expiry; resolve the chat's user (`wa.user_id`, creating the anonymous profile via `accounts.create_anonymous` if the chat has none); refuse suspended users (token → `denied`); set `user_id`, status `confirmed`; reply "You're signed in — return to the website. If this wasn't you, ignore this."

**4. Promote anonymous to regular.** On confirmation, if `user.is_anonymous`, set `is_anonymous=False`, keep projects and the existing balance, and top the balance up to `DEFAULT_CREDITS` once (idempotent ledger key `tg-login:<user_id>`), and apply registered retention to existing assets via the existing helpers. The chat's `WhatsAppAccount` becomes `linked`. *Alternative:* keep them anonymous and let the dashboard work anyway — rejected, anonymous quotas and short retention would silently apply to a signed-in person.

**5. QR on the client.** The login page renders the `deep_link` with the `qrcode` npm package (`toDataURL`) beside an "Open Telegram" button; it polls `status` every 2 s and redirects on `confirmed`. No server work for the QR. The nav and landing "Create account" slot become "Log in with Telegram" when registration is disabled; `/register` keeps its existing disabled behaviour; the public `/api/config` already exposes `telegram_bot`, which also tells the page whether login is available.

**6. Admin role.** New setting `ADMIN_TELEGRAM_IDS` (comma-separated Telegram user IDs). When a login is confirmed or a message arrives from a chat whose id is listed, set `role="admin"` (never demote automatically). The existing email-account admins keep the role.

**7. Admin API.** `GET /api/admin/users?q&limit&offset` joins `users` with `whatsapp_accounts` (channel from the `wa_id` prefix: `tg:` → telegram, otherwise whatsapp, none → web), counts projects and completed reels with grouped subqueries, and `last_seen = max(account.last_inbound_at, latest session created_at, user.updated_at)`; sorted by last seen. `PATCH /api/admin/users/{id}` accepts `{status?, credits_delta?}` (role changes stay config-driven); self-suspend refused; credit changes go through `credits.grant` and every action through `audit`. Suspended users are already rejected by `current_session` and the engine. No message bodies or asset URLs in responses.

**8. Admin page.** `app/admin/page.tsx` (client component using `useMe({required})`): table with channel badge, name/@username, projects, reels, credits, last seen, status and actions (suspend/reactivate, +10/−10 credits), search box, paging. Nav shows "Admin" only for `role === "admin"`; the API enforces it regardless.

## Risks / Trade-offs

- [Anyone can request tokens, spamming the table] → per-IP rate limit and a purge of expired tokens in the existing `purge_expired` maintenance task.
- [Link phishing: a victim presses Start on an attacker's link, signing the attacker's browser in as the victim] → inherent to link/QR logins; the bot reply names the action and says to ignore it otherwise, tokens are short-lived and single-use. Accepted.
- [Two processes polling the same bot token fight over `getUpdates` (HTTP 409)] → documented in `docs/TELEGRAM_SETUP.md`: one poller per token; give each developer their own bot (or use webhooks).
- [Promoting to regular raises credit exposure] → the top-up is one-time and idempotent; admins can adjust or suspend.
- [Telegram usernames can change or be absent] → admin list shows the display name plus `@username` when present, keyed by the stable chat id.

## Migration Plan

1. Add migration `0002_login_tokens`; deploy backend, then frontend.
2. Set `ADMIN_TELEGRAM_IDS` to the owner's Telegram ID (obtainable from `@userinfobot` or the admin audit log).
3. Rollback: downgrade drops `login_tokens`; no other data changes (promotion only flips `is_anonymous`).
