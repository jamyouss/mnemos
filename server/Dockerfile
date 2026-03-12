FROM python:3.12-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Install shared rag_core package first for layer caching
COPY packages/rag_core /app/packages/rag_core
RUN pip install --no-cache-dir /app/packages/rag_core

# Install server dependencies
COPY server/requirements.txt /app/server/requirements.txt
RUN pip install --no-cache-dir -r /app/server/requirements.txt

# Copy server source
COPY server/ /app/server/

EXPOSE 8100

HEALTHCHECK --interval=30s --timeout=10s --start-period=60s --retries=3 \
    CMD curl -f http://localhost:8100/health || exit 1

CMD ["python", "-m", "uvicorn", "server.main:create_app", "--factory", "--host", "0.0.0.0", "--port", "8100"]
