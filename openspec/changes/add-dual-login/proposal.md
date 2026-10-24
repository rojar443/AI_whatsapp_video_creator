# Proposal

> **SUPERSEDED** by `switch-to-telegram-clip-editing` (WhatsApp is replaced by Telegram, email/password login is dropped). Do not apply or archive.

## Why

ReelChat lives in WhatsApp, yet the website only accepts email + password, and the phone number typed at signup stays unverified until the user separately links it. Users should be able to sign in with just their WhatsApp number, while existing email + password users keep working.

## What Changes

- Add **WhatsApp login**: the website shows a one-time code, the user sends `LOGIN <code>` to the business number from their own phone, and the browser that requested the code is signed in. This also creates the account on first use (WhatsApp-first signup).
- Keep **email + password** register/login unchanged for users who prefer it.
- Make email and password optional on an account, so a WhatsApp-only account can exist; let users add the other method from the account page without losing access.
- A phone number counts as *claimed* only once verified (a linked WhatsApp account). Unverified typed-in numbers never block signup or take over anyone's account.
- A WhatsApp login for a number goes to the account that already has that number verified; it is never merged into an account that merely typed the number.
- Re-authentication for sensitive actions (account deletion, changing login methods) accepts a password **or** a fresh WhatsApp code.
- Web: login and register pages get a WhatsApp / Email choice; the account page manages login methods.
- **BREAKING (data model)**: `users.email` and `users.password_hash` become nullable.

## Capabilities

### New Capabilities
- `whatsapp-login`: Sign in or sign up by proving ownership of a WhatsApp number via a one-time code sent to the business number, plus the polling handoff that turns a confirmed code into a browser session.
- `account-identity`: How an account is identified and reached: optional email/password and phone credentials, when a phone is considered claimed, adding login methods, and re-authentication for sensitive actions.

### Modified Capabilities
<!-- none: openspec/specs is empty; existing email register/login and LINK behaviour is carried into account-identity where it changes -->

## Impact

- Backend: `app/models.py` (User nullable fields, new login-challenge table), `app/api/auth.py` (new endpoints), `app/api/me.py` (login-method management, delete confirmation), `app/conversation/engine.py` + `safety.py` (`LOGIN` command for any sender, linked or not), schema migration.
- Frontend: `app/login`, `app/register`, `app/account`, `lib/api.ts`, `lib/useMe.ts`.
- Docs: `docs/API.md`, `docs/ARCHITECTURE.md`.
- No new third-party dependency; no paid Meta template needed (the user initiates the chat).
