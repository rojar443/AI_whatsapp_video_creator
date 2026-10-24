#!/bin/bash
# All-in-one start for a first Railway deploy: API + Celery worker/beat + Telegram poller in ONE container.
# They share one volume at /app/var (uploads, renders), which Railway can only attach to a single service.
# When you outgrow this, split it into api / worker / telegram services and switch to STORAGE_BACKEND=s3.
set -e

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
