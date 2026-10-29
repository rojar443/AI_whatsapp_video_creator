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
# Extra provider SDKs: e.g. --build-arg EXTRAS=dev,s3,anthropic,openai
ARG EXTRAS=dev
COPY pyproject.toml ./
# dependencies only — the app itself runs from /app (no stub package in site-packages)
RUN uv pip install --system -r pyproject.toml $(echo ",${EXTRAS}" | sed 's/,\([a-z0-9]\)/ --extra \1/g')
COPY . .
RUN useradd --create-home --uid 1000 reelchat && mkdir -p /app/var && chown -R reelchat /app/var
USER reelchat

# Local default; hosting platforms can override PORT at runtime.
ENV PORT=8000
EXPOSE 8000
CMD ["sh", "-c", "alembic upgrade head && exec uvicorn app.main:app --host 0.0.0.0 --port \"${PORT:-8000}\" --proxy-headers --forwarded-allow-ips '*'"]
