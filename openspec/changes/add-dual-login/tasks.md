# Tasks

## 1. Schema and migrations

- [x] 1.1 Generate the baseline Alembic revision for the current models in `backend/alembic/versions/` and verify `alembic upgrade head` on an empty Postgres creates every table (compare against `Base.metadata`) - verified on SQLite with `alembic check`; Postgres run still to do
- [x] 1.2 Make `User.email` and `User.password_hash` nullable and add a `LoginChallenge` model (`app/models.py`); add the migration revision and verify upgrade and downgrade run cleanly - verified on SQLite; Postgres run still to do
- [x] 1.3 Audit every use of `user.email` / `user.password_hash` (serializers, admin API, email service, delete) and make them null-safe; verify `pytest` passes and add a test serialising a user with no email

## 2. WhatsApp login backend

- [x] 2.1 Add `POST /api/auth/whatsapp/start` (normalise phone, require `accept_terms`, create challenge, set `rc_login` poll-secret cookie, rate-limit per IP and per number) with tests for valid, invalid-number, no-terms and rate-limit cases
- [x] 2.2 Add the `LOGIN <code>` command in `safety.py` and route it first in `ConversationEngine._handle` for linked and unlinked senders; verify tests for correct code, wrong number, wrong/expired code, 5-attempt lockout and replies
- [x] 2.3 Implement account resolution on confirm (linked account → sign in; none → create user, grant signup credits once, link WhatsApp; typed-only phone ignored; suspended refused) and verify with tests for each scenario in `specs/whatsapp-login`
- [x] 2.4 Add `GET /api/auth/whatsapp/status` with one-time session redemption bound to the poll-secret cookie; verify tests for pending, expired, wrong secret, and double redemption
- [x] 2.5 Make email login treat a null `password_hash` like an unknown email (dummy hash, same 401); verify with a test comparing responses

## 3. Account identity and re-authentication

- [x] 3.1 Enforce "claimed only when verified" in registration and `start_link` (typed duplicates allowed, linking an already-linked number → 409) and verify with the two-accounts scenarios
- [x] 3.2 Add `reauth` challenges (create endpoint for signed-in users, confirm via `LOGIN`, single-use) and accept password or `reauth_challenge_id` in `/api/me/delete`; verify with tests for password, WhatsApp-only, wrong sender and expired proof
- [x] 3.3 Add `POST /api/me/credentials` to attach email + password to a WhatsApp-only account (re-auth, email conflict → 409, sends verification mail); verify with tests, including no account merging
- [x] 3.4 Add an end-to-end test: email user links WhatsApp, logs in by WhatsApp and lands in the same account with their projects and credits; unlinked email user gets a separate account
- [x] 3.5 Update `docs/API.md` and `docs/ARCHITECTURE.md` for the new endpoints, tables and the verified-phone rule; verify every new endpoint is documented

## 4. Web UI (branch `ui-web`)

- [ ] 4.1 Add API client types/functions for start, status and reauth in `frontend/lib/api.ts`; verify `npm run typecheck` passes
- [ ] 4.2 Rebuild `/login` and `/register` with a WhatsApp | Email toggle, code + "Open WhatsApp" button, 2 s polling, expiry and retry states; verify manually in Docker with the dev simulator that a WhatsApp login signs in and an email login still works
- [ ] 4.3 Extend `/account` with a login-methods card (add email + password, link WhatsApp) and WhatsApp-confirmed account deletion; verify both account types can delete
- [ ] 4.4 Extend the dev simulator so `LOGIN <code>` can be sent from the chosen test number; verify the full WhatsApp login works offline with `docker compose up`
- [ ] 4.5 Add a dashboard banner prompting email users to link WhatsApp; verify it disappears once linked

## 5. Integration

- [ ] 5.1 Run the full backend suite and a manual end-to-end pass of: WhatsApp signup, email signup, add method, link, WhatsApp login of a linked email user, and account deletion for both types
