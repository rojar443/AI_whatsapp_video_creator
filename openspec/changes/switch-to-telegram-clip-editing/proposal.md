# Proposal

## Why

ReelChat is moving from WhatsApp to Telegram: the Telegram Bot API is free, has no business verification, template or 24-hour-window rules, and gives every user a stable ID without needing their phone number. The product is also changing from "AI makes a video from an idea" to **editing the user's own footage**: a user uploads travel clips and says "make a cinematic adventure reel from these", and gets a finished vertical reel back. This change supersedes `add-dual-login` (WhatsApp-number login), which is no longer the right identity model.

## What Changes

- Replace the WhatsApp channel with a **Telegram bot**: webhook with secret-token verification, text, voice notes, video files, inline buttons, video delivery. Remove the WhatsApp adapter and WhatsApp-specific rules (templates, 24-hour window, phone linking).
- **Identity is the Telegram user ID.** Sign-up and login happen by opening the bot from a website deep link (`/start <token>`); no password and no phone number. **Email + password login is removed.** An optional email may be added later for receipts (out of scope here).
- **Clip uploads**: users send videos in the chat (up to Telegram's 20 MB bot download limit) or upload larger files on the website through a short-lived link the bot provides; clips attach to the user's current project.
- **Clip editing**: the conversation collects the story/vibe/length/platform, shows a plan that names the clips, and on explicit "yes" renders a vertical reel cut from the user's clips (best-moment selection, trims, transitions, music, optional captions). Revisions re-edit the same clips.
- Website: landing and dashboard describe the upload-and-edit flow; login becomes "Continue with Telegram"; project page shows clips and an upload area.
- **BREAKING**: WhatsApp tables/endpoints/messages and email+password auth are dropped. The product has no production data yet, so no data migration is provided.

## Capabilities

### New Capabilities
- `telegram-messaging`: Receiving and sending chat messages through a Telegram bot, including media and buttons.
- `telegram-login`: Signing in to and creating an account from the Telegram identity, and handing the session to the requesting browser.
- `account-identity`: What identifies an account (Telegram ID), what is deliberately not required, re-authentication, and deletion.
- `clip-uploads`: Receiving, validating, storing, listing and expiring the user's source video clips, from chat and from the website.
- `clip-editing`: Turning a project's clips and the user's brief into a vertical reel, with plan confirmation, credits and revisions.

### Modified Capabilities
<!-- none: openspec/specs is empty. add-dual-login is superseded and should not be archived. -->

## Impact

- Backend: `app/messaging/*` (new Telegram provider, remove `meta_cloud.py`), `app/api/webhooks.py`, `app/conversation/*` (chat-id based, buttons, video intake, plan text), `app/models.py` (telegram accounts, clips, login tokens; drop WhatsApp tables), `app/api/auth.py` / `me.py` (Telegram login, no passwords), new clips API, `app/video/*` (new clip-based provider using FFmpeg), `app/config.py` (bot token, bot username, webhook secret, upload limits), Alembic migrations.
- Frontend: landing, login/register, dashboard, project page, account, dev simulator (becomes a Telegram simulator).
- Docs: README, API, ARCHITECTURE, a new SETUP.
- External: a bot created in BotFather; for local runs a webhook tunnel or long-polling mode.
- Constraint: editing must run on CPU-only machines (no GPU models).
