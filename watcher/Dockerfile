FROM python:3.12-slim

WORKDIR /app

# Install watcher dependencies
COPY watcher/requirements.txt /app/watcher/requirements.txt
RUN pip install --no-cache-dir -r /app/watcher/requirements.txt

# Copy watcher source
COPY watcher/ /app/watcher/

CMD ["python", "-m", "watcher.main"]
