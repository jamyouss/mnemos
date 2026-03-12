FROM python:3.12-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Copy and install rag_core dependencies
COPY packages/rag_core /app/packages/rag_core
RUN pip install --no-cache-dir \
    "qdrant-client>=1.9.0" \
    "sentence-transformers>=2.6.0" \
    "tree-sitter>=0.22.0" \
    "tree-sitter-go>=0.21.0" \
    "pydantic>=2.6.0"

# Make rag_core importable
ENV PYTHONPATH="/app/packages:${PYTHONPATH}"

# Install server dependencies
COPY server/requirements.txt /app/server/requirements.txt
RUN pip install --no-cache-dir -r /app/server/requirements.txt

# Copy server source and scripts
COPY server/ /app/server/
COPY scripts/ /app/scripts/

EXPOSE 8100

HEALTHCHECK --interval=30s --timeout=10s --start-period=60s --retries=3 \
    CMD curl -f http://localhost:8100/health || exit 1

CMD ["python", "-m", "uvicorn", "server.main:create_app", "--factory", "--host", "0.0.0.0", "--port", "8100"]
