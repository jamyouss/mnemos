"""Query logging — append every retrieval call to a JSONL file for later analysis.

Each log line is a flat JSON object with:
    timestamp, query, top_files, top_scores, latency_ms, n_results, intent, tenant

The logger is fail-open: any disk error is swallowed so observability never
breaks the request path.
"""
from __future__ import annotations

import json
import logging
import threading
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger("mnemos.querylog")


class QueryLogger:
    """Thread-safe JSONL appender. Disabled by default."""

    def __init__(self, path: str | Path, enabled: bool = True) -> None:
        self._path = Path(path)
        self._enabled = enabled
        self._lock = threading.Lock()
        if self._enabled:
            try:
                self._path.parent.mkdir(parents=True, exist_ok=True)
            except OSError:
                logger.warning("Could not create query-log dir: %s", self._path.parent)
                self._enabled = False

    @property
    def enabled(self) -> bool:
        return self._enabled

    def log(
        self,
        query: str,
        results: list[Any],
        latency_ms: float,
        intent: str = "search",
        tenant: str | None = None,
        extra: dict | None = None,
    ) -> None:
        if not self._enabled:
            return

        try:
            top_files = []
            top_scores = []
            for r in results[:10]:
                top_files.append(getattr(r, "file_path", "") or (r.get("file_path") if isinstance(r, dict) else ""))
                score = getattr(r, "score", None)
                if score is None and isinstance(r, dict):
                    score = r.get("score")
                top_scores.append(float(score) if score is not None else 0.0)

            entry = {
                "ts": time.time(),
                "intent": intent,
                "query": query,
                "n_results": len(results),
                "top_files": top_files,
                "top_scores": top_scores,
                "latency_ms": round(latency_ms, 2),
            }
            if tenant:
                entry["tenant"] = tenant
            if extra:
                entry.update(extra)
        except Exception:
            return  # never break the caller

        try:
            line = json.dumps(entry, ensure_ascii=False)
            with self._lock, self._path.open("a", encoding="utf-8") as fh:
                fh.write(line + "\n")
        except OSError:
            # Disk full / read-only mount / etc. — drop the entry silently.
            pass
