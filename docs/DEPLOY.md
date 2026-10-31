# Deploy to a server

Works on any Linux VM that runs Docker (2 vCPU / 4 GB RAM is comfortable; FFmpeg is the heavy part). It needs an
**always-on VM**, not a "sleeping" web-app plan, because the Telegram poller and the video worker must keep running.

## 1. Get a VM
Any provider with a free or cheap VM works. Free options change often, so check current terms: Oracle Cloud "Always
Free" (full VM, needs a card for sign-up verification), or a small paid VPS (Hetzner and similar, a few euros a
month). Pick Ubuntu 22.04/24.04. Open **ports 443 and 80** (80 only fetches certificates and redirects to https) in the provider's firewall (and nothing else except SSH).
On Oracle images the VM's own firewall also blocks them by default: `sudo iptables -I INPUT -p tcp -m multiport --dports 80,443 -j ACCEPT`
(save with `sudo netfilter-persistent save`).

## 2. Install Docker
```bash
sudo apt update && sudo apt install -y docker.io docker-compose-v2 git
sudo usermod -aG docker $USER && newgrp docker
```

## 3. Get the code and configure
```bash
git clone https://github.com/rojar443/AI_whatsapp_video_creator.git && cd AI_whatsapp_video_creator
bash deploy/setup-env.sh     # builds .env: generates the secrets, asks for the bot token, AI key and server IP/domain
```
Fill in: `SECRET_KEY` and `POSTGRES_PASSWORD` (`openssl rand -hex 32`), `TELEGRAM_BOT_TOKEN`,
`TELEGRAM_BOT_USERNAME`, `GEMINI_API_KEY` (Google directly, the default) or
`OPENAI_API_KEY` (when choosing the Pollinations gateway), and the address block. **No domain?** use
`<server-ip>.sslip.io`, e.g. `SITE_ADDRESS=203-0-113-7.sslip.io` (dashes for the IP's dots), and set `WEB_BASE_URL`,
`PUBLIC_BASE_URL`, `CORS_ORIGINS` to `https://` + that name.

Keep the AI provider and model notation paired:

| Provider | Model | Key |
| --- | --- | --- |
| `gemini` (Google directly) | `gemini-3.8-flash` | `GEMINI_API_KEY` |
| `openai_compatible` (Pollinations gateway) | `google/gemini-3.8-flash` | `OPENAI_API_KEY` |

The Gemini adapter accepts the `google/` prefix for compatibility and removes it
before calling Google; the gateway adapter preserves its model IDs. A direct
Google 404 with `google/gemini-*` in older versions is a model routing mismatch.
Changing `.env.production.example` does not update a running server's `.env`.
For an existing Google deployment, set `AI_PROVIDER=gemini` and
`AI_MODEL=gemini-3.8-flash` in its actual environment. Also correct any nonblank
`AI_MODEL_VISION` or `AI_MODEL_SCRIPT` overrides (or leave them blank to inherit).
Keep the existing `GEMINI_API_KEY`. Rebuild/recreate the backend services to apply:

```bash
docker compose -f docker-compose.prod.yml up -d --build --force-recreate api worker telegram
```

Create a new project after restarting to verify vision selection and recorded
model usage. Old plans and fallback logs remain historical records.

## 4. Start
```bash
docker compose -f docker-compose.prod.yml up -d --build   # first build takes several minutes
docker compose -f docker-compose.prod.yml ps               # all services Up; api "healthy"
curl https://YOUR-ADDRESS/api/health                       # {"ok":true,...}
```
Then message your bot on Telegram. **Stop the poller on any other machine first** (your laptop): two pollers with
one token fight over messages.

## Operate
```bash
docker compose -f docker-compose.prod.yml logs -f worker telegram   # logs
git pull && docker compose -f docker-compose.prod.yml up -d --build # update
docker compose -f docker-compose.prod.yml exec postgres pg_dump -U reelchat reelchat > backup.sql   # backup
```
Uploads and reels live on the `media` Docker volume; back it up too, or move to S3-style storage for several servers.

## Security notes
* Postgres, Redis and the API are not published; only Caddy listens (80/443). Keep it that way.
* `.env` holds the bot token and AI key. Never commit it. Rotate any key that was ever pasted in a chat.
* The API starts only with a real `SECRET_KEY` in production. Registration is off by default.

## Not tested
This guide and `docker-compose.prod.yml` were validated (`docker compose config`) but not deployed to a real server
in this project, and ARM VMs (Oracle's free ones are ARM) are untested. Expect to fix one or two things on first run.
