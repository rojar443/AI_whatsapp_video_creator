#!/bin/bash
# All-in-one start for a first Railway deploy: API + Celery worker/beat + Telegram poller in ONE container.
# They share one volume at /app/var (uploads, renders), which Railway can only attach to a single service.
# When you outgrow this, split it into api / worker / telegram services and switch to STORAGE_BACKEND=s3.
set -e

# Fail fast with a readable message instead of a long traceback when Railway variables are missing.
missing=""
case "${DATABASE_URL:-}" in postgres://*|postgresql://*|postgresql+psycopg://*) ;; *) missing="$missing DATABASE_URL(must be the Postgres URL, e.g. \${{Postgres.DATABASE_URL}})";; esac
if [ "${TASK_BACKEND:-celery}" = "celery" ] && [ -z "${REDIS_URL:-}" ]; then missing="$missing REDIS_URL(e.g. \${{Redis.REDIS_URL}})"; fi
if [ "${APP_ENV:-}" = "production" ] && [ -z "${SECRET_KEY:-}" ]; then missing="$missing SECRET_KEY"; fi
if [ -n "$missing" ]; then
  echo "ERROR: missing or wrong Railway variables on this service:$missing" >&2
  # Show only the first characters (never the password) so an unresolved ${{...}} reference or a wrong scheme is visible.
  echo "  DATABASE_URL begins with: '${DATABASE_URL:0:12}'   REDIS_URL begins with: '${REDIS_URL:0:8}'" >&2
  echo "  (empty = the variable is not on THIS service; '\${{' = the reference name does not match a service)" >&2
  echo "See docs/RAILWAY.md, section 2." >&2
  exit 1
fi

alembic upgrade head

celery -A app.jobs.celery_app worker -B -Q default,media \
  --concurrency "${WORKER_CONCURRENCY:-2}" --loglevel INFO -s /app/var/celerybeat-schedule &

if [ -n "$TELEGRAM_BOT_TOKEN" ]; then
  python -m app.telegram_poller &   # exactly one poller per bot token, so never scale this service past 1 replica
fi

uvicorn app.main:app --host 0.0.0.0 --port "${PORT:-8000}" --proxy-headers &

# If any process exits, stop the container so Railway restarts all of them together.
wait -n
exit 1
