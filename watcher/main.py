from __future__ import annotations

import os
import time
import threading
import logging

import httpx
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler, FileSystemEvent

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("rag-watcher")

IGNORE_PATTERNS = [
    # Dependencies & package managers
    "node_modules/",
    ".pnpm-store/",
    "vendor/",
    # Version control
    ".git/",
    # Build outputs
    "dist/",
    "build/",
    ".nuxt/",
    ".output/",
    # Caches & tooling
    "__pycache__/",
    ".nx/",
    ".cache/",
    ".pytest_cache/",
    ".storybook/",
    # IDE & editors
    ".idea/",
    ".vscode/",
    # Virtualenvs
    ".venv/",
    "venv/",
    # Test artifacts
    "test-results/",
    "coverage/",
]

IGNORE_EXTENSIONS = [
    ".min.js", ".map", ".lock",
    # Logs & generated data
    ".log",
    # Binary & compiled
    ".pyc", ".o", ".a",
    # Images & fonts
    ".png", ".jpg", ".jpeg", ".gif", ".ico", ".svg",
    ".woff", ".woff2", ".ttf", ".eot",
    # Archives & binaries
    ".pdf", ".zip", ".tar", ".gz", ".exe", ".bin", ".so", ".dylib",
]

IGNORE_FILENAMES = [
    "CHANGELOG.md",
    "pnpm-lock.yaml",
    "package-lock.json",
    "yarn.lock",
    "poetry.lock",
    ".last-run.json",
]

RAG_SERVER_URL = os.getenv("MNEMOS_SERVER_URL", "http://rag-server:8100")
DEBOUNCE_MS = int(os.getenv("WATCHER_DEBOUNCE_MS", "2000"))


def should_ignore(path: str) -> bool:
    basename = path.rsplit("/", 1)[-1] if "/" in path else path
    return (
        any(pattern in path for pattern in IGNORE_PATTERNS)
        or any(path.endswith(ext) for ext in IGNORE_EXTENSIONS)
        or basename in IGNORE_FILENAMES
    )


class PathRouter:
    def __init__(self, codebase_root: str, config_root: str) -> None:
        self._codebase_root = codebase_root.rstrip("/")
        self._config_root = config_root.rstrip("/")

    def route(self, abs_path: str) -> tuple[str, str] | None:
        if abs_path.startswith(self._config_root + "/"):
            rel = abs_path[len(self._config_root) + 1:]
            if rel.startswith("skills/"):
                return "rag_skills", rel
            if rel.startswith("docs/"):
                return "rag_docs", rel
            return None

        if abs_path.startswith(self._codebase_root + "/"):
            rel = abs_path[len(self._codebase_root) + 1:]
            if rel.startswith("moby/"):
                return "rag_code_moby", rel
            if rel.startswith("trevio/"):
                return "rag_code_trevio", rel
            if rel.startswith("infra/") or rel.startswith("github-cicd/"):
                return "rag_code_infra", rel
            return None

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
        if should_ignore(abs_path):
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
