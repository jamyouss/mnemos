# syntax=docker/dockerfile:1.7

# -----------------------------------------------------------------------------
# Stage 1 — build: install all Python dependencies into a virtualenv that we
# can copy into a slim runtime stage. This keeps the build toolchain out of
# the final image (~1.2 GB saved on torch+transformers).
# -----------------------------------------------------------------------------
FROM python:3.12-slim AS builder

ENV PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONDONTWRITEBYTECODE=1

# Build-time toolchain for native wheels (tree-sitter, numpy, torch on linux/arm64).
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential \
        git \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /build

# Create a venv we'll copy into the runtime stage.
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:${PATH}"

# Install dependencies first — separate layer so a source-only change does
# NOT bust the heavyweight pip cache (torch ~1 GB).
COPY server/requirements.txt /build/server/requirements.txt
COPY packages/core/pyproject.toml /build/packages/core/pyproject.toml
RUN pip install --upgrade pip && \
    pip install -r /build/server/requirements.txt && \
    pip install qdrant-client \
                sentence-transformers \
                tree-sitter \
                tree-sitter-go \
                tree-sitter-typescript \
                pydantic \
                pyyaml \
                httpx

# Now copy actual sources + install in editable mode.
COPY packages/core /build/packages/core
RUN pip install --no-deps -e /build/packages/core


# -----------------------------------------------------------------------------
# Stage 2 — runtime: tiny base image + the prebuilt venv + just the server.
# -----------------------------------------------------------------------------
FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/opt/venv/bin:${PATH}" \
    PYTHONPATH="/app/packages"

# Only the runtime curl is needed (healthcheck + manual debug).
RUN apt-get update && apt-get install -y --no-install-recommends \
        curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Bring in the venv from the builder. Note: we re-copy the core package
# source into /app so the editable-install paths still resolve at runtime.
COPY --from=builder /opt/venv /opt/venv
COPY packages/core /app/packages/core
COPY server/ /app/server/
COPY scripts/ /app/scripts/
COPY config/ /app/config/

# Re-install editable in the runtime image too — fast because deps are cached.
RUN pip install --no-deps -e /app/packages/core

EXPOSE 8100

HEALTHCHECK --interval=30s --timeout=10s --start-period=60s --retries=3 \
    CMD curl -f http://localhost:8100/health || exit 1

# Mnemos OCI metadata — surfaced by `docker inspect` and on Docker Hub.
LABEL org.opencontainers.image.title="Mnemos server" \
      org.opencontainers.image.description="Self-hosted memory layer for AI coding agents (FastAPI + MCP)." \
      org.opencontainers.image.source="https://github.com/jamyouss/mnemos" \
      org.opencontainers.image.licenses="MIT"

CMD ["python", "-m", "uvicorn", "server.main:create_app", "--factory", "--host", "0.0.0.0", "--port", "8100"]
