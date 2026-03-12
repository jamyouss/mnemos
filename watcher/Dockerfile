FROM python:3.12-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# Install shared rag_core package first for layer caching
COPY packages/rag_core /app/packages/rag_core
RUN pip install --no-cache-dir /app/packages/rag_core

# Install watcher dependencies
COPY watcher/requirements.txt /app/watcher/requirements.txt
RUN pip install --no-cache-dir -r /app/watcher/requirements.txt

# Copy watcher source
COPY watcher/ /app/watcher/

CMD ["python", "-m", "watcher.main"]
