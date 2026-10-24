# Telegram input channel

Send clips, photos and voice notes to a Telegram bot instead of (or as well as) WhatsApp. It runs the same
conversation, plan, confirmation, quotas and pipeline. It uses the official Bot API with **long polling**, so no
public URL or tunnel is needed — it works on localhost.

## 1. Create the bot (2 minutes, free)
1. In Telegram, open **@BotFather** → send `/newbot` → choose a name and a username ending in `bot`.
2. BotFather replies with a **token** (`123456789:AA…`). Treat it like a password.
3. Optional: `/setdescription`, `/setuserpic`, and `/setcommands` with:
   ```
   help - how it works
   status - progress of your reel
   new - start a new reel
   cancel - stop what's in progress
   retry - retry a failed reel
   ```

## 2. Run
```bash
# .env
TELEGRAM_BOT_TOKEN=123456789:AA…        # paste your token here, not in chat or git
docker compose up -d                    # api, worker, web
docker compose --profile telegram up -d telegram
docker compose logs -f telegram         # "telegram poller started: @YourBot"
```
Check: `curl localhost:8100/api/health/providers` (dev) shows the bot under `messaging.telegram`.

## 3. Use it
Open your bot in Telegram → **Start** → send 3–10 videos/photos → *"That's all. Make it a cinematic travel
reel with a calm voiceover."* → tap **✅ Proceed** → the reel arrives as a Telegram video.

* Send clips **as a file** (📎 → File) to keep full quality; normal sends are compressed by Telegram.
* Voice notes work (needs `TRANSCRIPTION_PROVIDER=gemini` + key; `mock` can't transcribe real audio).
* To keep reels on your dashboard: log in → **Connect Telegram** → open the link → **Start**. Reels you already
  made in that chat move into your history. (Registering needs no phone number if you only use Telegram.)

## Limits and behaviour
* The Bot API lets bots download files up to **20 MB**; larger clips are rejected with a "file is too large"
  message — ask users to trim them or send shorter clips. (Self-hosting the Bot API server lifts this to 2 GB.)
* Bots can send videos up to 50 MB; reels are already compressed to ≤15 MB, so they're sent as attachments.
* No 24-hour window or templates on Telegram: finished reels are sent straight away.
* Only private chats are served; groups and channels are ignored.
* Anonymous quotas apply until the chat is linked to an account (same as WhatsApp).
* Chats are stored as `tg:<chat_id>`; the bot only replies to people who message it first.

## Website login with Telegram (link or QR)
The website's **Log in with Telegram** button shows a one-time `t.me` link and the same link as a QR code (scan it with
your phone when you're on a computer). Press **Start** in the bot and the browser is signed in. Anonymous bot users keep
their reels and credits. Needs `TELEGRAM_BOT_TOKEN` and `TELEGRAM_BOT_USERNAME`.

## Admin
Put your Telegram user id in `ADMIN_TELEGRAM_IDS` (ask @userinfobot for it), log in with Telegram once, and an **Admin**
tab appears listing everyone who has used the app.

## One poller per bot token
Telegram lets only **one** process call `getUpdates` for a token. If two developers (or a Docker `telegram` service and a
local run) use the same token, both see `409 Conflict`. Create a separate bot per developer with @BotFather, or use
webhooks in production.
