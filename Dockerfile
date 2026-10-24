# Default image when Railway builds from the REPO ROOT with no Root Directory and no RAILWAY_DOCKERFILE_PATH: the backend.
# Identical to Dockerfile.backend. For the website set RAILWAY_DOCKERFILE_PATH=Dockerfile.web on that service.
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 \
    UV_HTTP_TIMEOUT=600 UV_CONCURRENT_DOWNLOADS=4 UV_SYSTEM_PYTHON=1 UV_COMPILE_BYTECODE=0

# ffmpeg (libass for captions, zoompan/xfade) + fonts. APT_EXTRA=espeak-ng adds the offline demo voice.
ARG APT_EXTRA=""
RUN apt-get -o Acquire::Retries=10 -o Acquire::http::Timeout=60 update \
    && apt-get -o Acquire::Retries=10 -o Acquire::http::Timeout=60 install -y --no-install-recommends \
        ffmpeg fonts-dejavu-core curl ${APT_EXTRA} \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir --default-timeout=600 uv

WORKDIR /app
# Extra provider SDKs: e.g. EXTRAS=dev,s3,anthropic,openai
ARG EXTRAS=dev
COPY backend/pyproject.toml ./
RUN uv pip install --system -r pyproject.toml $(echo ",${EXTRAS}" | sed 's/,\([a-z0-9]\)/ --extra \1/g')
COPY backend/ .
RUN useradd --create-home --uid 1000 reelchat && mkdir -p /app/var && chown -R reelchat /app/var
USER reelchat

# API + Celery worker/beat + Telegram poller in one container (see backend/start_all.sh)
CMD ["bash", "start_all.sh"]
