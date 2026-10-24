FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 \
    UV_HTTP_TIMEOUT=600 UV_CONCURRENT_DOWNLOADS=4 UV_SYSTEM_PYTHON=1 UV_COMPILE_BYTECODE=0

# ffmpeg (libass for captions, zoompan/xfade), espeak-ng (offline fallback voice), fonts.
RUN apt-get update && apt-get install -y --no-install-recommends \
        ffmpeg espeak-ng fonts-dejavu-core curl \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir --default-timeout=600 uv

WORKDIR /app
# Extra provider SDKs: e.g. --build-arg EXTRAS=dev,s3,anthropic,openai
ARG EXTRAS=dev
COPY pyproject.toml ./
RUN mkdir app && touch app/__init__.py \
    && uv pip install --system ".[${EXTRAS}]" \
    && rm -rf app
COPY . .
RUN useradd --create-home --uid 1000 reelchat && mkdir -p /app/var && chown -R reelchat /app/var
USER reelchat

EXPOSE 8000
CMD ["sh", "-c", "alembic upgrade head && uvicorn app.main:app --host 0.0.0.0 --port 8000 --proxy-headers"]
