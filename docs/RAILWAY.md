# Deploy on Railway

One Railway project with **three** services: `web`, `backend` and Postgres. (Redis runs inside `backend`; add a Railway Redis service later if you want one, then set `REDIS_URL=${{Redis.REDIS_URL}}`.) The `backend` service runs the API,
the Celery worker and the Telegram poller together (`backend/start_all.sh`), so they can share one volume.

```
Telegram ──▶ backend (poller + API + worker) ◀──▶ Postgres, Redis, volume /app/var
Browser  ──▶ web (public domain) ──/api/*──▶ backend (private network)
```

Only `web` gets a public domain; `backend` stays private. The browser talks to `web`, which proxies `/api/*`.

> **Fastest path (one service, one URL):** the repo-root `Dockerfile` builds the website + API + worker + bot into a
> single image. Create one service from your fork's `public` branch (Root Directory empty), add Postgres and a volume at
> `/app/var`, paste `docs/railway/app.env`, and generate a domain. That domain shows the UI. The rest of this guide
> (separate `backend` and `web` services, with `docs/railway/backend.env` and `web.env`) is the split setup.

## 0. Before you start
* Push this branch to GitHub (Railway deploys from GitHub). Use `main`, or the `public` branch if you want a separate deploy branch.
* Create a **production bot** with @BotFather (do not reuse your dev bot: two processes polling one token fail with
  `409 Conflict`). Keep the token for step 4.
* Your Telegram user id (ask @userinfobot) for `ADMIN_TELEGRAM_IDS`.

## 1. Create the project
1. railway.com → **New Project** → **Deploy from GitHub repo** → pick this repo. Railway creates one service.
2. In the project: **+ New** → **Database** → **PostgreSQL**. (Redis is optional, see above.)

## 2. The `backend` service
Settings → **Source → Root Directory**: `/backend`. (The repo root only has sub-folders, so Railpack finds no project
and fails with "could not pick a build plan". With the root directory set, Railway uses `backend/Dockerfile`.)
Rename the service to `backend`.

* The start command (`bash start_all.sh`), the Dockerfile builder and the health check come from `backend/railway.json`.
  If you ever see "railpack process exited with an error", the service is not building from `/backend`: re-check the
  Root Directory, the branch, and under Settings → Build that the builder is **Dockerfile**.
* **Settings → Networking:** do **not** generate a public domain.
* **Volumes:** + New → Volume, mount path `/app/var` (uploaded clips and finished reels live here).
* **Variables** (use the Variables tab → Raw editor; `${{...}}` are Railway references):

```
APP_ENV=production
SECRET_KEY=<long random string, 32+ chars>
PORT=8080                              # the API's port on Railway's private network
RAILWAY_RUN_UID=0                      # lets the app write to the volume
DATABASE_URL=${{Postgres.DATABASE_URL}}
# REDIS_URL is optional: leave it unset to use the Redis built into the backend image
TASK_BACKEND=celery
STORAGE_LOCAL_DIR=/app/var/storage
WORK_DIR=/app/var/work
PLAYBOOK_DIR=/app/playbooks
MUSIC_LIBRARY_DIR=/app/music

WEB_BASE_URL=https://<your-web-domain>
PUBLIC_BASE_URL=https://<your-web-domain>
CORS_ORIGINS=https://<your-web-domain>
COOKIE_SECURE=true
REGISTRATION_ENABLED=false

TELEGRAM_BOT_TOKEN=<production bot token>
TELEGRAM_BOT_USERNAME=<bot username without @>
ADMIN_TELEGRAM_IDS=<your telegram id>

# the API refuses to start in production with the default WhatsApp secrets, even though WhatsApp is off
WHATSAPP_ENABLED=false
WHATSAPP_APP_SECRET=<any random string>
WHATSAPP_VERIFY_TOKEN=<any random string>

# AI: start with offline stand-ins (placeholder voice, rule-based plans), then switch to the real providers
AI_PROVIDER=rules
TRANSCRIPTION_PROVIDER=mock
TTS_PROVIDER=mock
# real providers instead: AI_PROVIDER=gemini + GEMINI_API_KEY, TRANSCRIPTION_PROVIDER=gemini,
# TTS_PROVIDER=elevenlabs + ELEVENLABS_API_KEY
```
`<your-web-domain>` is known after step 3; add these three URL variables then and redeploy.

### If "Railpack failed to prepare the build" keeps happening
Railway is not picking up the Root Directory. Skip it: leave **Root Directory empty** and instead add a variable that
names a Dockerfile in the repo root, built with the whole repo as context:
* `backend` service: nothing to set (the repo-root `Dockerfile` is the backend), or `RAILWAY_DOCKERFILE_PATH=Dockerfile.backend` (its `CMD` runs `bash start_all.sh`)
* `web` service: `RAILWAY_DOCKERFILE_PATH=Dockerfile.web`

Everything else (variables, volume, domains) stays the same. In that mode `backend/railway.json` is not read, so the
health check is not applied; that is fine.

## 3. The `web` service
1. In the project: **+ New** → **GitHub Repo** → the same repo. Rename it `web`.
2. **Source → Root Directory:** `/frontend`.
3. **Variables:**
```
BACKEND_URL=http://backend.railway.internal:8080
PORT=3100
```
   `BACKEND_URL` must be the backend's **private** address and its `PORT` (8080, set in step 2). The rewrite target
   is baked in at build time, so changing `BACKEND_URL` needs a redeploy of `web`.
4. **Settings → Networking → Generate Domain** (target port `3100`). That URL is `<your-web-domain>` above.

## 4. Check it
* `backend` logs: `telegram poller started: @<bot>` and no `409` warnings; `alembic` migrated; the API is up.
* `https://<your-web-domain>/api/health` returns `{"ok": true, ...}`.
* Open the site → **Log in with Telegram** → press Start in the bot. Your id in `ADMIN_TELEGRAM_IDS` gives you the Admin tab.
* The development ID box on the login page is hidden in production (the endpoint returns 404).

## Notes and limits
* **Memory:** rendering uses FFmpeg; give `backend` at least 2 GB RAM (lower `WORKER_CONCURRENCY`, default 2, if it runs out).
* **One backend replica only:** the poller must be a single process per bot token.
* **Growing up:** split `backend` into `api`, `worker` (`celery -A app.jobs.celery_app worker -B -Q default,media`) and
  `telegram` (`python -m app.telegram_poller`) services, build them with `EXTRAS=dev,s3`, and set `STORAGE_BACKEND=s3`
  with a private S3-compatible bucket, because a Railway volume attaches to one service only.
* **Costs:** Railway bills per usage; the worker and volume are the main cost. Set a usage limit in project settings.
* **Secrets:** keep tokens only in Railway variables, never in git.
