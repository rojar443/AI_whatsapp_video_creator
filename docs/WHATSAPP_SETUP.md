# Connecting a real WhatsApp number

What you'll end up with in `.env`:

```
MESSAGING_PROVIDER=meta
WHATSAPP_PHONE_NUMBER_ID=...      # from API Setup
WHATSAPP_BUSINESS_ACCOUNT_ID=...  # from API Setup (WABA ID)
WHATSAPP_ACCESS_TOKEN=...         # system-user token (step 5); the 24 h temporary token works for a first try
WHATSAPP_APP_SECRET=...           # App settings → Basic → App secret
WHATSAPP_VERIFY_TOKEN=...         # any random string you choose, also typed into Meta (step 7)
WHATSAPP_BUSINESS_NUMBER=...      # the test number, digits only, e.g. 15551234567
PUBLIC_BASE_URL=https://...       # your tunnel URL (step 6)
GEMINI_API_KEY=...
ELEVENLABS_API_KEY=...
```

Meta changes its screens often. If a label differs, follow the official guide:
https://developers.facebook.com/documentation/business-messaging/whatsapp/get-started

## 1. Meta developer account
1. Go to https://developers.facebook.com, log in with your Facebook account and click **Get started** to register
   as a developer (verify phone/email if asked).

## 2. Create the app
1. **My Apps → Create app**.
2. Use case: **Connect with customers through WhatsApp** (or app type **Business**).
3. Give it a name (e.g. `ReelChat Dev`), pick or create a **Business portfolio**, create the app.

## 3. Get the test number and IDs
1. In the app, open **WhatsApp → API Setup** (sometimes "Quickstart").
2. Meta gives you a free **test phone number**. Copy:
   * **Phone number ID** → `WHATSAPP_PHONE_NUMBER_ID`
   * **WhatsApp Business Account ID** → `WHATSAPP_BUSINESS_ACCOUNT_ID`
   * the test number itself (digits only) → `WHATSAPP_BUSINESS_NUMBER`
3. Click **Generate access token** — this temporary token lasts ~24 h. Fine for the first test
   (`WHATSAPP_ACCESS_TOKEN`); replace it in step 5.

## 4. Add your phone as a recipient
1. Still on API Setup, under **To**, choose **Manage phone number list** and add your personal WhatsApp number.
2. Enter the code WhatsApp sends you. Test numbers can only message up to ~5 verified recipients.
3. Click **Send message** to receive the `hello_world` template — this proves the number works.

## 5. Permanent token (system user)
1. Open https://business.facebook.com/settings → **Users → System users → Add** (role: Admin).
2. **Assign assets**: add your app (full control) and your WhatsApp account (full control).
3. **Generate new token** → select your app → permissions `whatsapp_business_messaging` and
   `whatsapp_business_management` → expiry *Never* → copy into `WHATSAPP_ACCESS_TOKEN`.

## 6. Public HTTPS URL with Cloudflare Tunnel
Meta must reach `https://<public-host>/api/webhooks/whatsapp`.

**Quick tunnel (no account, URL changes on every restart)** — already in `docker-compose.yml`:
```bash
docker compose up -d tunnel
docker compose logs tunnel | grep -o 'https://[a-z0-9-]*\.trycloudflare\.com'
```
Put that URL in `PUBLIC_BASE_URL`, restart `api` and `worker`, and update the webhook in Meta (step 7) whenever it changes.

**Named tunnel (stable URL, needs a free Cloudflare account and a domain on Cloudflare)**:
1. Cloudflare dashboard → **Zero Trust → Networks → Tunnels → Create a tunnel** (type *Cloudflared*).
2. Copy the tunnel **token** into `.env` as `CLOUDFLARE_TUNNEL_TOKEN`.
3. Add a **Public hostname**, e.g. `reels.yourdomain.com` → service `http://api:8000`.
4. `docker compose --profile named-tunnel up -d tunnel-named`; set `PUBLIC_BASE_URL=https://reels.yourdomain.com`.

## 7. Configure the webhook
1. App → **WhatsApp → Configuration → Webhook → Edit**.
2. **Callback URL**: `https://<public-host>/api/webhooks/whatsapp`
3. **Verify token**: the same string as `WHATSAPP_VERIFY_TOKEN`. Click **Verify and save** (the API must be running).
4. Under **Webhook fields**, **Subscribe** to `messages`.
5. **App secret**: App settings → Basic → App secret → **Show** → `WHATSAPP_APP_SECRET`
   (used to check the `X-Hub-Signature-256` of every webhook call).

## 8. Message template for the 24-hour rule
Free-form and media messages are only allowed within 24 h of the user's last message. If a reel finishes later,
the app first sends an approved template.
* Until you have your own, set `WHATSAPP_NOTIFY_TEMPLATE=hello_world` and `WHATSAPP_TEMPLATE_PARAMS=0`.
* Then in **WhatsApp Manager → Message templates → Create**: category **Utility**, name `reel_ready`,
  language English, body e.g. `Your reel "{{1}}" is ready. Reply to this message to receive it.` Submit, and once
  approved set `WHATSAPP_NOTIFY_TEMPLATE=reel_ready`, `WHATSAPP_TEMPLATE_PARAMS=1`.

## 9. API keys
* **Gemini**: https://aistudio.google.com/apikey → `GEMINI_API_KEY`.
* **ElevenLabs**: https://elevenlabs.io → Profile → API keys → `ELEVENLABS_API_KEY`. Pick a voice in the Voice
  Library and put its ID in `ELEVENLABS_VOICE_ID` (otherwise the default voice is used).

## 10. Test
1. `docker compose up -d` and confirm `https://<public-host>/api/health` returns `{"ok": true}`.
2. From your verified phone, send the test number 2–6 short clips or photos, then
   *"That's all. Make it a cinematic travel reel with a calm voiceover."*
3. You should get an acknowledgement, a plan with buttons, progress messages, and the reel.

## Policy notes
* Since 15 Jan 2026 Meta doesn't allow general-purpose AI assistants as the primary product on WhatsApp.
  ReelChat only makes reels and politely declines open-ended chat. Re-check the Business Solution Terms before launch.
* Going live (real customers) needs business verification and a real phone number added to the WABA.
