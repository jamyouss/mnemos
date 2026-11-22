# syntax=docker/dockerfile:1.7

# The `mnemos` CLI plus the knowledge connectors, packaged to run
# `mnemos ingest confluence|jira|web|git` as a cron job / Kubernetes CronJob.
# Talks to the server over HTTP only: no model, no Qdrant client in here.
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    MNEMOS_URL="http://rag-server:8100"

WORKDIR /app

# The git connector clones repositories with the git binary.
RUN apt-get update && apt-get install -y --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/*

COPY cli /app/cli
COPY packages/connectors /app/packages/connectors
RUN pip install --no-cache-dir /app/cli /app/packages/connectors

# Nothing here needs root.
RUN useradd --create-home --uid 10001 mnemos
USER mnemos

LABEL org.opencontainers.image.title="Mnemos CLI" \
      org.opencontainers.image.description="Mnemos command-line client and knowledge connectors (Confluence, Jira, web, git) for scheduled ingestion." \
      org.opencontainers.image.source="https://github.com/jamyouss/mnemos" \
      org.opencontainers.image.licenses="MIT"

ENTRYPOINT ["mnemos"]
CMD ["--help"]
