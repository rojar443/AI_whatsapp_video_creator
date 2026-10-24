# Design

## Context

- Auth today (`app/api/auth.py`): email + password only; `users.email` is `NOT NULL UNIQUE`, `users.password_hash` is `NOT NULL`, `users.phone_e164` is typed at signup, indexed but not unique.
- WhatsApp ownership is proven by the existing `LINK <code>` flow: `POST /api/me/whatsapp/link` stores a hashed code on `whatsapp_accounts`, and `ConversationEngine._handle_unlinked` matches it. `whatsapp_accounts.wa_id` is unique, so a linked row is already a one-number-one-owner claim.
- `ConversationEngine._handle` routes by the sender's `WhatsAppAccount`; unlinked senders only reach `_handle_unlinked`.
- Account deletion (`/api/me/delete`) verifies a password, which breaks for accounts without one.
- The `Dockerfile` runs `alembic upgrade head`, but `backend/alembic/versions/` does not exist in the repo, while tests build the schema with `create_all`. A baseline migration must exist before this change can add its own.

See proposal.md for motivation.

## Goals / Non-Goals

**Goals:**
- WhatsApp-first signup and login with no outbound paid message and no password.
- Reuse the existing ownership proof (inbound message from the number) and the existing session/CSRF machinery.
- No way for an unverified number to lock out or hijack another person.

**Non-Goals:**
- SMS OTP, social login, passkeys, TOTP.
- Merging two existing accounts.
- Email-based password reset (separate change).
- Changing the video conversation flow.

## Decisions

**1. New `login_challenges` table instead of overloading `whatsapp_accounts.link_code_*`.**
Columns: `id`, `purpose` (`login` | `reauth`), `wa_id`, `code_hash`, `poll_secret_hash`, `status` (`pending` | `confirmed` | `consumed`), `attempts`, `expires_at`, `user_id` (set on confirm, or for `reauth` at creation), `accept_terms_at`, `created_at`. The link code is single-slot per number and bound to a known user; a login happens before any user exists and several browsers may try at once. *Alternative:* reuse the link columns — rejected, it cannot express "no user yet" and would clobber a pending LINK.

**2. Code hashing and matching mirror `link_code_hash`:** `sha256(wa_id:code:secret_key)`, 6 digits, TTL from settings (reuse `link_code_ttl_minutes`), 5 wrong attempts per challenge. The engine looks up open challenges by sender `wa_id` (taken from the signed webhook, not from message text), so a code sent from another number cannot match.

**3. Browser handoff with a poll secret.** `POST /api/auth/whatsapp/start` returns `challenge_id` and sets an HttpOnly `rc_login` cookie holding a random poll secret (only its hash is stored). `GET /api/auth/whatsapp/status?id=` returns `pending` | `expired`, or, when confirmed and the cookie matches, starts the session via the existing `_start_session` and marks the challenge `consumed` in the same transaction. *Alternative:* return the session from the webhook — impossible, the webhook is Meta's request, not the browser's. Anyone who learns the challenge id still lacks the secret.

**4. Account resolution on confirm (the "claimed only when verified" rule).** In `_handle_login`:
1. If a `WhatsAppAccount` with this `wa_id` is `linked` to a user → that user (if `active`; suspended → refuse).
2. Otherwise create a `User` (email/password null, `phone_e164 = +wa_id`), grant signup credits with the existing idempotent key, create or attach the `WhatsAppAccount` as `linked`, and audit it.
Users whose phone is merely typed (no linked `WhatsAppAccount`) are never matched. Rationale: the typed number is attacker-controllable; the inbound WhatsApp message is the only proof. Consequence, documented in the UI: an email user must link WhatsApp once before WhatsApp login reaches *their* account.

**5. Make `users.email` and `users.password_hash` nullable.** Email stays unique (Postgres allows many NULLs; the SQLite test DB too). `phone_e164` stays non-unique and gets no new constraint; uniqueness of a *verified* number is already enforced by `whatsapp_accounts.wa_id`. `start_link` keeps returning 409 when the number is linked to another user. *Alternative:* make `phone_e164` unique — rejected, it is exactly the unverified-number lockout we want to avoid.

**6. `LOGIN` is parsed before the linked/unlinked branch** in `ConversationEngine._handle`, using a regex like `_LINK_RE` (`login <6 digits>`), so linked users can confirm too and the text never reaches the video agent.

**7. Re-authentication.** Sensitive endpoints (`/api/me/delete`, add-email) accept either `password` or `reauth_challenge_id`. A `reauth` challenge is created by a signed-in user, bound to their `user_id` and linked number, confirmed with `LOGIN <code>` like a login, and redeemed once by the endpoint. Password accounts may still use the password.

**8. Adding a method.** Email + password for a WhatsApp-only user goes through `POST /api/me/credentials` (re-auth, then set email, hash password, send the existing verification email; email conflict → 409). Linking WhatsApp for email users remains the existing `LINK` flow. Removing methods is out of scope for now; the "never zero methods" rule is enforced by simply not offering removal, and kept as a requirement for future work.

**9. Constant-time login unchanged:** a null `password_hash` is verified against the dummy hash so responses and timing match an unknown email.

**10. Frontend.** Login/register get a WhatsApp | Email toggle. The WhatsApp tab shows the number field and terms checkbox, then the code plus an "Open WhatsApp" `wa.me` button, and polls `status` every 2 s until confirmed/expired, then redirects to `/dashboard`. The account page lists methods and offers "Add email + password" or "Link WhatsApp". The dev simulator (`/dev/whatsapp`) is extended so `LOGIN` can be tested locally.

## Risks / Trade-offs

- [Email user has not linked WhatsApp and logs in by WhatsApp → gets a second, empty account] → Dashboard banner and signup copy tell email users to link WhatsApp; no silent merge by design.
- [Code phishing: attacker gets a victim to send `LOGIN <attacker code>`] → The session goes only to the attacker's browser and signs in as the victim's number. Mitigation: the bot reply states "You are signing in to ReelChat on a browser — if this wasn't you, ignore this", short TTL, per-IP and per-number rate limits; shared-secret binding is not possible without a second factor. Accepted residual risk, same class as every "send this code" flow.
- [Spam: anyone can request codes for any number] → No outbound message is ever sent to the number; only inbound messages count, so there is nothing to spam. Rate-limit challenge creation per IP and per number.
- [Meta webhook delay makes polling feel slow] → 2 s polling, 15 min expiry, clear "waiting for your message" state.
- [Nullable email affects other code that assumes it is set (emails, admin list, serializers)] → audit all `user.email` uses during implementation; tests cover a WhatsApp-only user end to end.
- [Missing baseline Alembic revision] → first task generates it from the current models before adding this change's revision.

## Migration Plan

1. Generate the baseline revision for the current schema (so `alembic upgrade head` works on a fresh DB); for any already-deployed DB, stamp it at the baseline.
2. Add one revision: create `login_challenges`; `ALTER users.email, users.password_hash DROP NOT NULL`.
3. Deploy backend, then frontend. Rollback: downgrade drops the table and restores NOT NULL (fails if WhatsApp-only users exist — acceptable, documented in the revision).
