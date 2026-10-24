# ONE image for Railway: website + API + Celery worker + Telegram poller. Built from the REPO ROOT.
# The public domain serves the website (Next.js on $PORT); it proxies /api/* to the API on 127.0.0.1:8000 inside the container.
# To run them as separate services instead, use Dockerfile.backend and Dockerfile.web (see docs/RAILWAY.md).

# ---- website build -------------------------------------------------------------------------------------------
FROM node:22-slim AS web-build
WORKDIR /web
COPY frontend/package.json frontend/package-lock.json* ./
RUN npm ci --no-audit --no-fund || npm install --no-audit --no-fund
COPY frontend/ .
ENV NEXT_TELEMETRY_DISABLED=1
# /api/* is rewritten to the API in the same container (resolved when the site is built)
ENV BACKEND_URL=http://127.0.0.1:8000
RUN npm run build

# ---- runtime: backend image + the built website --------------------------------------------------------------
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 \
    UV_HTTP_TIMEOUT=600 UV_CONCURRENT_DOWNLOADS=4 UV_SYSTEM_PYTHON=1 UV_COMPILE_BYTECODE=0

# ffmpeg (libass for captions, zoompan/xfade) + fonts + redis (Celery broker, used when REDIS_URL is not set)
ARG APT_EXTRA=""
RUN apt-get -o Acquire::Retries=10 -o Acquire::http::Timeout=60 update \
    && apt-get -o Acquire::Retries=10 -o Acquire::http::Timeout=60 install -y --no-install-recommends \
        ffmpeg fonts-dejavu-core curl redis-server libstdc++6 ${APT_EXTRA} \
    && rm -rf /var/lib/apt/lists/*

# Node runtime for the website (copied from the official image: same Debian base, so it runs here)
COPY --from=web-build /usr/local/bin/node /usr/local/bin/node

RUN pip install --no-cache-dir --default-timeout=600 uv

WORKDIR /app
# Extra provider SDKs: e.g. EXTRAS=dev,s3,anthropic,openai
ARG EXTRAS=dev
COPY backend/pyproject.toml ./
RUN uv pip install --system -r pyproject.toml $(echo ",${EXTRAS}" | sed 's/,\([a-z0-9]\)/ --extra \1/g')
COPY backend/ .
COPY --from=web-build /web/.next/standalone /app/web
COPY --from=web-build /web/.next/static /app/web/.next/static
RUN useradd --create-home --uid 1000 reelchat && mkdir -p /app/var && chown -R reelchat /app/var
USER reelchat

# website on $PORT, API on 127.0.0.1:8000, worker, poller (see backend/start_all.sh)
CMD ["bash", "start_all.sh"]
