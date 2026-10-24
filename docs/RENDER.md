# Render deployment

This deployment keeps video files on Render. No S3 bucket is required.

## Layout

- Existing backend: `AI_whatsapp_video_creator`, Singapore, branch `agenticorg_dep`.
- Existing database: `reelchat-postgres`, PostgreSQL 18, same region/workspace.
- New `reelchat-queue`: managed Key Value, 256 MB, private connections only,
  `noeviction` policy. The paid queue preserves queued work across restarts.
- New `reelchat-web`: Next.js Docker web service, with a runtime proxy to the backend.
- One 5 GB disk on the backend at `/var/data`. API, Celery worker, one Beat
  scheduler, and optional Telegram poller run in that same container.

The backend uses `backend/Dockerfile.render`. It includes FFmpeg, FFprobe,
DejaVu fonts and all optional provider SDKs. On startup it verifies that the
disk is mounted, creates writable media/work/scheduler directories, drops root
privileges, runs migrations, verifies Redis and FFmpeg, then starts the child
processes. If any child exits, the service exits so Render can restart it.
Shutdown signals reach the worker and its child processes.

Do not scale the backend beyond one instance or add a separate media worker with
this storage layout. A disk cannot be shared between services. Deploys interrupt
service while the disk moves to the new instance; the worker uses late
acknowledgements to allow unfinished tasks to be redelivered.

## Cost and capacity

Verified against Render's pricing on October 24, 2026:

| Resource | Initial plan | Monthly list price |
| --- | --- | --- |
| Backend (existing service, upgraded) | 1 CPU / 2 GB | $25 |
| Media disk | 5 GB | $1.25 |
| Key Value | 256 MB | $10 |
| Frontend | Free | $0 |
| Postgres (existing) | Free | $0 |
| Total | | $36.25 |

This excludes metered usage, workspace charges, taxes, and third-party providers.
The approved backend plan is 1 CPU / 2 GB; the previous 512 MB plan is tight for
combined API, Python workers, and FFmpeg. Worker concurrency and FFmpeg threads
are both one. Verify memory usage with representative clips before launch.
The free frontend can sleep when idle.

The existing free Postgres expires **November 23, 2026**. Upgrade it before that
date for continued service and backups. This Blueprint intentionally preserves
its current plan and external-access settings.

## Deployment sequence

1. Validate `render.yaml` with the current JSON schema and, when authenticated,
   `render blueprints validate render.yaml`.
2. Commit and push the deployment files to `agenticorg_dep`. The existing backend
   auto-deploys pushes, so configure the disk, environment and Dockerfile first
   or turn auto-deploy off during setup. Never push `.env` or `.tools`.
3. Apply `render.yaml` from that branch in the confirmed Render workspace.
   It matches the existing backend/database by name; review that it reuses them.
   The Blueprint needs Dashboard/API access for the disk and existing service
   settings; the connected MCP tools alone cannot perform these operations.
4. The frontend gets `BACKEND_HOSTPORT` from Render's backend service reference.
   Its proxy adds `http://` at runtime. Do not set a competing `BACKEND_URL`
   unless deliberately overriding that private-network destination.
5. Set `WEB_BASE_URL` and `CORS_ORIGINS` to the actual frontend HTTPS URL.
   Keep `PUBLIC_BASE_URL` set to the existing backend HTTPS URL. If custom domains
   are used, update all three accordingly.
6. Verify the backend has a strong `SECRET_KEY`. Keep `APP_ENV=production` and
   `COOKIE_SECURE=true`. Configure AI, TTS, transcription and messaging settings
   explicitly; copy only relevant provider settings from local `.env`, never its
   local database, Redis, media paths or development flags. A `TELEGRAM_BOT_TOKEN`
   starts exactly one poller; stop any local poller using that token first.
   For real WhatsApp set `MESSAGING_PROVIDER=meta`, `WHATSAPP_ENABLED=true`,
   tokens and webhook secrets. Register the backend `/api/webhooks/whatsapp`
   URL with Meta. Configure SMTP if enabling email registration/recovery.
7. Monitor the specific deployment until `live`. Confirm `/api/health` returns
   HTTP 200 and `db: true` on both URLs. A database outage returns HTTP 503.
8. Submit a short video through the enabled channel, confirm the worker completes
   it, and download/play the result. Restart the backend and confirm media remains
   available. Inspect logs for worker, Beat and optional poller startup.

Local videos and local database contents are **not** migrated by deployment.
Treat any desired data import as a separate operation with a database backup.

## Access required for deployment from the coding session

The Render connector can inspect services, deploy code and set environment
variables. It cannot attach a disk, change this existing service's Dockerfile,
or apply a Blueprint. For full automation, provide `RENDER_API_KEY` through the
session environment or the ignored root `.env`; never paste it into chat or
commit it. Alternatively, apply the Blueprint once in the Dashboard, then the
connector can monitor and redeploy it. GitHub access must permit updates to
`rojar443/AI_whatsapp_video_creator`.

References: [Blueprint specification](https://render.com/docs/blueprint-spec),
[persistent disks](https://render.com/docs/disks),
[pricing](https://render.com/pricing).
