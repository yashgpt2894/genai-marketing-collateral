# Cloud Run container for the Generative Collateral API.
# Personal project — synthetic sample data only; no confidential content.
FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PYTHONPATH=/app

WORKDIR /app

# Copy source (see .dockerignore — excludes .venv, data/, .env, caches, samples).
COPY . /app

# Install the app + deps, INCLUDING the cloud extra (Firestore/GCS/Pub-Sub) the
# production backends need. Harmless for the local-store quick path.
RUN pip install --upgrade pip && pip install ".[cloud]"

# Cloud Run provides $PORT (defaults to 8080). Bind to it.
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8080}"]
