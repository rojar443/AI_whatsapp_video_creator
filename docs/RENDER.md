# Render deployment with local media

This deployment uses two web services in Singapore:

| Service | Runs |
| --- | --- |
| `AI_whatsapp_video_creator` | API, Celery worker, Beat scheduler, optional Telegram poller |
| `reelchat-web` | Next.js website; proxies `/api/*` to the backend privately |

The backend reuses its existing 5 GB disk at `/var/data` and connects to the existing
`reelchat-postgres` and `reelchat-queue`. The Blueprint references those datastores without
declaring or provisioning replacements. All resources must be in the same workspace and
region, with compatible environment isolation settings.

## Apply the deployment

1. Commit the deployment files and push to `prod_v2_render`. Pushing alone does not switch the
   existing service to the new Dockerfile: apply the Blueprint below as well.
2. In Render, create a Blueprint from this repository, select **prod_v2_render**, and use
   `render.yaml`. If the backend is already managed by another Blueprint, update that
   Blueprint's branch/path instead; do not manage the same service from two Blueprints.
3. Review the sync plan: update the existing backend and disk, create `reelchat-web`,
   and reference the existing database and queue. Do not apply a plan that replaces
   the existing disk or provisions duplicate datastores. The frontend uses the paid
   `0.5c-512mb` plan; the backend retains `1c-2g`.
4. Preserve the existing backend `SECRET_KEY` (changing it invalidates signed links).
   Provide the public URL settings below. On an existing service, confirm these values
   directly in Environment; Blueprint `sync: false` entries do not update them on later syncs.
5. Keep your AI/messaging settings on the backend. Existing variables omitted from the
   Blueprint are preserved. The Render image includes Gemini, OpenAI, and Anthropic SDKs.
6. Stop any local or old deployment using this bot token before starting this Telegram
   poller. Exactly one poller and one scheduler should run.
7. Apply the Blueprint. Once the frontend URL is assigned, update the website URL
   settings to that exact URL and deploy the backend again if necessary.

### Backend environment

| Setting | Value |
| --- | --- |
| `SECRET_KEY` | Existing production secret, at least 16 characters; use a long random value for a new deployment |
| `PUBLIC_BASE_URL` | Public backend HTTPS URL, currently `https://ai-whatsapp-video-creator-ygko.onrender.com` |
| `WEB_BASE_URL` | Exact public HTTPS URL of `reelchat-web` |
| `CORS_ORIGINS` | Same website origin (no trailing slash); comma-separated if necessary |
| `TELEGRAM_BOT_TOKEN`, `TELEGRAM_BOT_USERNAME` | Existing bot settings; poller starts only when token is set |
| `AI_PROVIDER`, provider key/model settings | Your current AI configuration |
| `TTS_PROVIDER`, `ELEVENLABS_API_KEY` | Your speech configuration |
| `TRANSCRIPTION_PROVIDER` | Your transcription configuration |

The Blueprint wires `DATABASE_URL` and `REDIS_URL` from existing resources. The launcher
normalizes the PostgreSQL URL to the installed psycopg3 driver. Check that the queue uses
`noeviction` in Render; it should not discard pending jobs to make room.

If WhatsApp is enabled, also set its production app secret and verify token. The Render
image does not include the optional offline espeak voice; use ElevenLabs or mock TTS.

The frontend receives `BACKEND_HOSTPORT` automatically and needs no backend credentials.
Remove any stale frontend `BACKEND_URL`, because an explicit URL takes precedence.

## Startup and recovery

The Render-specific `backend/Dockerfile.render` leaves the ordinary local Dockerfile and
Compose setup intact. At startup its launcher:

1. Requires the persistent disk, prepares `storage`, `work`, and `beat` directories,
   and drops root privileges to UID 1000.
2. Applies database migrations once, before any backend process starts. Migrations stay
   in startup because no child should consume work against an outdated schema.
3. Checks Redis and FFmpeg, then replaces itself with Supervisor as PID 1.

Supervisor restarts each failed process independently, with bounded startup retries.
The worker consumes `default,media` with concurrency 1; `FFMPEG_THREADS=1` reduces CPU
contention. These are concurrency controls, not hard per-process memory/CPU isolation.
Beat writes its schedule to the persistent disk. Shutdown stops intake before the worker,
and terminates process groups within the disk-backed service's fixed shutdown window.
The worker gets 12 seconds to stop; long video jobs can be interrupted and redelivered.

After 20 failed startup retries, a process enters `FATAL`: inspect its logs and fix the
configuration. The API health check is not proof that background processes are healthy.
In the backend Shell, inspect or restart individual processes with:

```sh
supervisorctl -c /tmp/reelchat-supervisord.conf status
supervisorctl -c /tmp/reelchat-supervisord.conf restart worker
```

## Verify after deployment

- Backend `/api/health` returns `{"ok":true,"db":true,"env":"production"}`.
- Frontend `/api/health` returns the same response through its proxy.
- Supervisor reports API, worker and scheduler `RUNNING`, plus Telegram if configured.
- Log in through Telegram, upload a short clip, approve it, and confirm the finished
  video is delivered and downloadable from the website.
- Confirm `/var/data/storage` contains the media. Restart the backend and confirm the
  existing video remains downloadable.

Back up media and Postgres separately. A disk-backed backend remains single-instance;
backend redeployments interrupt all its processes briefly. Host failure or container OOM
can still affect the whole backend. The frontend has its own lifecycle. No S3 migration,
Telegram webhook conversion, or other application architecture changes are required.

## Validate before syncing

```sh
render blueprints validate render.yaml
```

References: [Blueprint specification](https://render.com/docs/blueprint-spec),
[persistent disks](https://render.com/docs/disks),
[private networking](https://render.com/docs/private-network), and
[Supervisor configuration](https://supervisord.org/configuration.html).
