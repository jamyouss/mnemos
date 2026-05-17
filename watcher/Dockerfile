# syntax=docker/dockerfile:1.7

# Watcher is much lighter than the server — just watchdog + httpx + a tiny
# Python file. Single-stage build is enough; final image stays under 200MB.
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH="/app/packages"

WORKDIR /app

# Install runtime deps (watcher) + the minimal slice of the core package it
# needs (just core.collections for path routing).
COPY watcher/requirements.txt /app/watcher/requirements.txt
RUN pip install --no-cache-dir -r /app/watcher/requirements.txt && \
    pip install --no-cache-dir pydantic pyyaml

# Bring in the core package (lazy import; we only use core.collections + core.projects)
COPY packages/core /app/packages/core
COPY watcher/ /app/watcher/

LABEL org.opencontainers.image.title="Mnemos watcher" \
      org.opencontainers.image.description="Filesystem watcher that pushes index updates to a Mnemos server." \
      org.opencontainers.image.source="https://github.com/jamyouss/mnemos" \
      org.opencontainers.image.licenses="MIT"

CMD ["python", "-m", "watcher.main"]
