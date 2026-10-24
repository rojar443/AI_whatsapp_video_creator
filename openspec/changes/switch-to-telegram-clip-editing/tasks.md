# Tasks

## 1. Schema and cleanup

- [ ] 1.1 Replace the WhatsApp models with `TelegramAccount`, `LoginToken`, `ButtonToken` and `UserClip`; drop `email`, `password_hash`, `phone_e164`, `email_verified_at` from `User`; regenerate a single baseline Alembic revision (removing the uncommitted `0001`/`0002`) and verify `alembic upgrade head` + `alembic check` pass on an empty database
- [ ] 1.2 Remove WhatsApp code and config (`messaging/meta_cloud.py`, WhatsApp webhook, link/LOGIN commands, `WHATSAPP_*` settings, email/password auth endpoints) and verify `import app.main` works and the remaining test suite collects
- [ ] 1.3 Add Telegram and upload settings (`TELEGRAM_BOT_TOKEN`, `TELEGRAM_BOT_USERNAME`, `TELEGRAM_WEBHOOK_SECRET`, `TELEGRAM_MODE`, clip limits, retention days) to `config.py` and `.env.example`; verify defaults let the app start offline

## 2. Telegram messaging

- [ ] 2.1 Implement `TelegramProvider` (send text, inline buttons, video upload, `getFile` download, blocked-bot detection) and verify with unit tests against a mocked HTTP transport
- [ ] 2.2 Add `POST /api/webhooks/telegram` with secret-token check, update parsing (text, voice, video, document video, callback query, unsupported) and `update_id` idempotency; verify 401 without secret and no double processing on redelivery
- [ ] 2.3 Add button tokens (≤64-byte `callback_data`) and wire confirm/edit/cancel/retry through them with ownership and nonce checks; verify stale, foreign and unknown tokens do nothing
- [ ] 2.4 Add `TELEGRAM_MODE=polling` (`getUpdates` loop in the worker) and convert the dev simulator to send Telegram-shaped updates; verify the full chat flow runs offline

## 3. Telegram login and identity

- [ ] 3.1 Implement `POST /api/auth/telegram/start`, bot handling of `/start <token>` (with in-bot confirm button) and `GET /api/auth/telegram/status` with single-use, cookie-bound redemption; verify with tests for valid, expired, reused, foreign-browser and suspended cases
- [ ] 3.2 Implement account resolution (existing Telegram ID → same account; new → create with signup credits once; bare `/start` creates and later web login finds it); verify with tests
- [ ] 3.3 Implement account page data (Telegram name/username, credits, reachable flag) and deletion confirmed by an in-bot button; verify wrong-chat and expired confirmations delete nothing
- [ ] 3.4 Update `docs/API.md` for the new auth/account endpoints and verify every new route is documented

## 4. Clip uploads

- [ ] 4.1 Implement clip intake service (ffprobe validation, size/duration/count/total limits, storage, DB row) and verify with fixtures for valid, undecodable and over-limit files
- [ ] 4.2 Handle chat videos (video and document) via `getFile`, attach to the current/new draft project, reply with count and length, and send the website upload link for files over 20 MB; verify both paths
- [ ] 4.3 Add `POST /api/projects/{id}/clips` (session) and the signed-link upload endpoint (project-scoped, streamed, expiring) plus list/reorder/delete; verify foreign, expired and rendering-project cases
- [ ] 4.4 Add retention purge and delete-on-account-delete for clips; verify files are removed from storage
- [ ] 4.5 Document upload endpoints and limits in `docs/API.md`

## 5. Clip editing

- [ ] 5.1 Implement clip analysis (proxy, shot boundaries, sharpness/exposure/motion scores) with per-clip caching; verify on generated test clips that boundaries and scores are produced
- [ ] 5.2 Implement segment selection by pace and length across clips (upload order, minimum variety); verify with unit tests for target length, faster pace giving shorter segments, and too-short footage
- [ ] 5.3 Implement rendering (vertical fit incl. rotation and landscape, crossfades, music mix under original audio, optional captions) and validate the MP4; verify with FFmpeg tests (skipped when FFmpeg is missing)
- [ ] 5.4 Register `ClipEditProvider` as the default video provider with admin switchability and progress stages; verify a project with clips renders end to end and a failed render refunds credits once

## 6. Conversation integration

- [ ] 6.1 Update the brief flow and plan message for clip projects (clips listed, no script/voice questions, defaults), keeping the deterministic confirmation gate; verify with the requirements and e2e tests
- [ ] 6.2 Support revisions that reuse existing clips (faster pace, change opening/ending hints) as a new revision with new confirmation; verify revision 2 renders without re-upload
- [ ] 6.3 Update reply templates and the Telegram `/start`, `/help`, `/status` commands; verify with e2e conversation tests using the simulator

## 7. Web UI (light and funky)

- [ ] 7.1 Replace the theme in `globals.css` with a light, playful design system (bright background, bold accents, chunky outlines and offset shadows, sticker badges, friendly display font) and drop dark mode; verify with screenshots of landing, login and dashboard at desktop and phone widths
- [ ] 7.2 Rebuild the landing page around the upload-and-edit story with a Telegram chat demo and "Continue with Telegram"; verify copy no longer mentions WhatsApp
- [ ] 7.3 Replace login/register with a single Telegram login (button, waiting state with polling, expiry/retry); verify against the local bot or simulator
- [ ] 7.4 Add the project page clip gallery with upload area (drag and drop, progress), reorder and remove, and the signed `/upload` page for links from the bot; verify uploading a clip appears in the project
- [ ] 7.5 Update dashboard, account page (Telegram identity, delete via bot) and the simulator page; verify `npm run typecheck` and `npm run build` pass

## 8. Integration

- [ ] 8.1 Update `README.md`, `docs/ARCHITECTURE.md`, and add `docs/SETUP.md` (BotFather steps, polling vs webhook, running locally); verify a new developer can follow them
- [ ] 8.2 Run the whole backend suite and a manual end-to-end pass: login via Telegram, send clips in chat, upload a large clip on the web, confirm the plan, receive the reel, request a revision

## Workflow follow-up

- Mark `add-dual-login` as superseded (do not archive it) once this change is accepted.
