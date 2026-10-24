# Proposal

## Why

`main` is now Telegram-first: people message the bot without an account, and website registration (email + password) is switched off by default. That leaves the website with no way in for anyone, so users cannot see their reels or credits. Telegram is the identity they already use, so the website should sign them in through it, on a phone with a tap or on a computer by scanning a QR code. The owner also needs an admin view of everyone who has used the app, which `main` no longer has.

## What Changes

- **Telegram web login**: the login page shows "Continue with Telegram" and a QR code of the same one-time link. Pressing Start in the bot signs in the browser that asked, with no password or email. It works for people who have only ever used the bot (anonymous Telegram profiles) and for linked accounts.
- A Telegram login promotes an anonymous Telegram profile to a normal account (registered quotas and retention, kept projects and credits) so nothing they made in the chat is lost.
- The website nav and landing page show "Log in with Telegram" instead of the disabled registration link. Email login stays for existing email accounts and is not required.
- **Admin tab**: admins see every person who has used the app, from any channel (Telegram, WhatsApp, website), with activity and credits, and can search, suspend, and adjust credits. Admin status is granted from configured Telegram IDs and by role.
- Tests, docs and local-run notes cover the new flows (including "only one process may poll a bot token").

## Capabilities

### New Capabilities
- `telegram-web-login`: Signing in to the website through the Telegram bot using a one-time deep link or QR code, and handing the session to the requesting browser.
- `admin-users`: The admin-only area that lists and manages all users who have used the app.

### Modified Capabilities
<!-- none: openspec/specs is empty -->

## Impact

- Backend: new `login_tokens` table + migration, `app/api/auth.py` (Telegram start/status), `app/conversation/engine.py` (`/start login_<token>`), `app/services/accounts.py` (promotion), new `app/api/admin.py`, `app/config.py` (admin Telegram IDs), `app/main.py` (router, public config), tests.
- Frontend: `app/login/page.tsx` (new), `components/Nav.tsx`, `components/StartButton.tsx`/landing, `app/admin/page.tsx` (new), new `qrcode` dependency, `lib/api.ts` types.
- Docs: `docs/API.md`, `docs/TELEGRAM_SETUP.md`.
- Supersedes the earlier `add-dual-login` and `switch-to-telegram-clip-editing` plans, which overlapped with the work already on `main` and have been removed.
