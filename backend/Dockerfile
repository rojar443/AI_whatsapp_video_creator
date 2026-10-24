FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1

# ffmpeg (with libass for captions), espeak-ng (free offline TTS), fonts incl. Indic scripts.
RUN apt-get update && apt-get install -y --no-install-recommends \
        ffmpeg espeak-ng fonts-dejavu-core fonts-lohit-deva fonts-lohit-mlym fonts-lohit-taml curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY pyproject.toml ./
RUN mkdir app && touch app/__init__.py && pip install ".[all,dev]" && rm -rf app
COPY . .
RUN useradd --create-home --uid 1000 reelchat && mkdir -p /app/var && chown -R reelchat /app/var
USER reelchat

EXPOSE 8000
CMD ["sh", "-c", "alembic upgrade head && uvicorn app.main:app --host 0.0.0.0 --port 8000 --proxy-headers"]
