from __future__ import annotations

import os
import time
import threading
import logging

import httpx
from core.collections import COLLECTIONS
from core.path_filter import should_skip_path
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler, FileSystemEvent

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("rag-watcher")

RAG_SERVER_URL = os.getenv("MNEMOS_SERVER_URL", "http://rag-server:8100")
DEBOUNCE_MS = int(os.getenv("WATCHER_DEBOUNCE_MS", "2000"))


def should_ignore(path: str) -> bool:
    """Thin alias kept for backwards compatibility with existing tests.

    The actual policy lives in :func:`core.path_filter.should_skip_path`.
    """
    return should_skip_path(path)


class PathRouter:
    def __init__(self, codebase_root: str, config_root: str) -> None:
        self._codebase_root = codebase_root.rstrip("/")
        self._config_root = config_root.rstrip("/")

    def route(self, abs_path: str) -> tuple[str, str] | None:
        """Map a filesystem path to (collection, rel_path).

        Skills / docs match the explicit path_prefixes from core.collections.
        Everything else under the codebase mount goes to the single
        `mnemos_code` collection — the project tag is added at index time
        by the server, not by the watcher.
        """
        # Config-side routing (skills / docs)
        if abs_path.startswith(self._config_root + "/"):
            rel = abs_path[len(self._config_root) + 1:]
            for coll in COLLECTIONS:
                if not coll.path_prefixes:
                    continue
                for prefix in coll.path_prefixes:
                    if rel.startswith(prefix) and coll.name in ("mnemos_skills", "mnemos_docs"):
                        return coll.name, rel
            return None

        # Code-side routing: a single collection for all repos. The server's
        # Indexer will derive the project tag from the path or from the YAML
        # override loaded at startup.
        if abs_path.startswith(self._codebase_root + "/"):
            rel = abs_path[len(self._codebase_root) + 1:]
            return "mnemos_code", rel

        return None


class DebouncedHandler(FileSystemEventHandler):
    def __init__(self, router: PathRouter, debounce_seconds: float = 2.0) -> None:
        self._router = router
        self._debounce = debounce_seconds
        self._pending: dict[str, tuple[str, str, str]] = {}
        self._timer: threading.Timer | None = None
        self._lock = threading.Lock()

    def on_created(self, event: FileSystemEvent) -> None:
        if not event.is_directory:
            self._queue(event.src_path, "created")

    def on_modified(self, event: FileSystemEvent) -> None:
        if not event.is_directory:
            self._queue(event.src_path, "modified")

    def on_deleted(self, event: FileSystemEvent) -> None:
        if not event.is_directory:
            self._queue(event.src_path, "deleted")

    def on_moved(self, event: FileSystemEvent) -> None:
        if not event.is_directory:
            self._queue(event.src_path, "deleted")
            if hasattr(event, "dest_path"):
                self._queue(event.dest_path, "created")

    def _queue(self, abs_path: str, event_type: str) -> None:
        if should_skip_path(abs_path):
            return
        route = self._router.route(abs_path)
        if route is None:
            return

        collection, rel_path = route
        with self._lock:
            self._pending[abs_path] = (collection, rel_path, event_type)
            if self._timer:
                self._timer.cancel()
            self._timer = threading.Timer(self._debounce, self._flush)
            self._timer.start()

    def _flush(self) -> None:
        with self._lock:
            pending = dict(self._pending)
            self._pending.clear()

        for abs_path, (collection, rel_path, event_type) in pending.items():
            try:
                httpx.post(
                    f"{RAG_SERVER_URL}/internal/reindex",
                    json={
                        "file_path": abs_path,
                        "event": event_type,
                        "collection": collection,
                    },
                    timeout=30,
                )
                logger.info(f"Indexed {event_type}: {abs_path} -> {collection}")
            except Exception as e:
                logger.error(f"Failed to index {abs_path}: {e}")


def main() -> None:
    codebase_root = os.getenv("CODEBASE_PATH", "/data/codebase")
    config_root = os.getenv("CLAUDE_CONFIG_PATH", "/data/claude-config")
    debounce = DEBOUNCE_MS / 1000.0

    router = PathRouter(codebase_root=codebase_root, config_root=config_root)
    handler = DebouncedHandler(router=router, debounce_seconds=debounce)

    observer = Observer()
    observer.schedule(handler, codebase_root, recursive=True)

    if os.path.isdir(config_root):
        observer.schedule(handler, config_root, recursive=True)

    logger.info(f"Watching: {codebase_root}, {config_root}")
    logger.info(f"Debounce: {debounce}s")
    observer.start()

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        observer.stop()
    observer.join()


if __name__ == "__main__":
    main()
