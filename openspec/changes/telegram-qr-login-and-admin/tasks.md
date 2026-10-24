# Tasks

## 1. Schema and settings

- [x] 1.1 Add the `LoginToken` model and Alembic revision `0002_login_tokens` (revises `0001`); verify `alembic upgrade head` runs on an empty database (`alembic check` still reports a drift that already exists in `0001`: the `fk_conversations_active_project` constraint)
- [x] 1.2 Add `ADMIN_TELEGRAM_IDS` to `config.py` and `.env.example`, and expire old tokens in the existing purge task; verify settings load (the purge test for expired tokens is still to do)

## 2. Telegram login backend

- [x] 2.1 Add `POST /api/auth/telegram/start` (terms, rate limit, 503 when no bot, `rc_login` cookie, deep link `login_<token>`); verify tests for valid, no-terms, rate-limited and unconfigured cases
- [x] 2.2 Handle `/start login_<token>` in the engine for private Telegram chats (open/expired/reused/suspended, resolves or creates the chat's user, applies admin IDs); verify with engine tests using the Telegram update parser
- [x] 2.3 Promote anonymous profiles on confirmation (regular flag, one-time credit top-up, retention, linked chat) and verify an anonymous user keeps projects and credits
- [x] 2.4 Add `GET /api/auth/telegram/status` with single-use, cookie-bound redemption through `_start_session`; verify pending, expired, foreign-browser and double-redeem cases
- [x] 2.5 Document the new endpoints and the one-poller-per-token rule in `docs/API.md` and `docs/TELEGRAM_SETUP.md`

## 3. Admin backend

- [x] 3.1 Add `app/api/admin.py` with `GET /api/admin/users` (search, paging, channel, project/reel counts, last seen, no message or media data) and register the router; verify 403 for non-admins and that bot-only users appear
- [x] 3.2 Add `PATCH /api/admin/users/{id}` (suspend/reactivate, credit delta, no self-lockout, ledger and audit entries); verify with tests including suspended users losing web access
- [x] 3.3 Document the admin endpoints in `docs/API.md`

## 4. Web UI (branch `ui-web`)

- [x] 4.1 Add the `qrcode` dependency, API types and the new `/login` page (terms checkbox, Open Telegram button, QR, polling, expiry/retry, unavailable state); verify `npm run typecheck`, the QR renders and decodes in the browser; scanning it with a real phone is still to do (needs a bot only one process polls)
- [x] 4.2 Update Nav, landing and `StartButton` to offer "Log in with Telegram" (registration disabled) and show the Admin tab only for admins; verify with screenshots as a normal user and as an admin
- [x] 4.3 Build the `/admin` page (user table, search, paging, suspend/reactivate, credit buttons) in the light funky theme; verified with one logged-in admin at a narrow width; seeded multi-user and phone-width checks are still to do
- [ ] 4.4 Show Telegram identity (name, @username) on the account page and handle the dashboard for promoted users; verify an anonymous bot user sees their earlier reels after logging in

## 5. Integration

- [x] 5.1 Run the backend test suite and a manual end-to-end pass (done with a simulated Start press; the real-bot pass is still to do): message the bot, log in on the web by link and by QR, see the reel, log in as the admin and find the user in the admin tab
