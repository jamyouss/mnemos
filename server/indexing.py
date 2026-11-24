"""What the index went through: the dashboard's Indexing page.

Two kinds of records share the `mnemos_index_events` collection:

- `file`: one file indexed, deleted, skipped or failed, by whichever channel
  sent it (watcher, git hook, connector, CLI, API, MCP);
- `job`: one bulk reindex (`POST /api/reindex`, MCP `mnemos_reindex`), its
  progress updated as files go, so a run is visible from start to end.

A bulk reindex records its job and its failures, not every file: a full walk
of the codebase is tens of thousands of files. Recording is best effort and
never fails the indexing it describes.
"""
from __future__ import annotations

import logging
import time
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Query, Request

from core.event_log import EventLog
from core.indexer import path_dirs
from server.audit import client_of
from server.principal import principal_of, require_admin

logger = logging.getLogger("mnemos.indexing")
indexing_router = APIRouter()

INDEX_EVENTS_COLLECTION = "mnemos_index_events"
INDEX_EVENT_FIELDS = ("kind", "op", "collection", "channel", "actor", "state", "path_dirs")

# Channels whose last activity the page shows, in its order.
CHANNELS = ("watcher", "hook", "connector", "cli", "dashboard", "mcp", "api", "script")
# A running job whose progress has not moved for this long lost its pod.
STALLED_AFTER = 120.0
_FLUSH_SECONDS = 2.0
_KEPT_ERRORS = 20


def _now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def file_event(
    log: EventLog | None,
    *,
    op: str,
    collection: str,
    file_path: str,
    channel: str,
    actor: str,
    chunks: int = 0,
    duration_ms: int | None = None,
    error: str | None = None,
    job_id: str | None = None,
) -> None:
    """Record what happened to one file; never raises."""
    if log is None:
        return
    try:
        log.record({
            "kind": "file",
            "op": op,
            "collection": collection,
            "file_path": file_path,
            "path_dirs": path_dirs(file_path),
            "channel": channel,
            "actor": actor,
            "chunks": chunks,
            "duration_ms": duration_ms,
            "error": error[:500] if error else None,
            "job_id": job_id,
        }, wait=False)
    except Exception:
        logger.warning("index event not stored: %s %s", op, file_path, exc_info=True)


def channel_of(request: Request, default: str | None = None) -> str:
    """The sending client; `default` when it did not name itself (the watcher predates the header)."""
    channel = client_of(request)
    return default if channel == "api" and default else channel


class ReindexJob:
    """One bulk reindex as a `job` record, its counters flushed every few seconds."""

    def __init__(self, log: EventLog | None, *, collection: str, path: str, full: bool,
                 actor: str, channel: str) -> None:
        self._log = log
        self.collection = collection
        self.actor = actor
        self.channel = channel
        self.counts = {"total": 0, "processed": 0, "chunks": 0, "failed": 0, "empty": 0}
        self.errors: list[dict[str, str]] = []
        self._flushed = 0.0
        self.id: str | None = None
        if log is None:
            return
        try:
            self.id = log.record({
                "kind": "job", "state": "running", "collection": collection, "path": path,
                "full": full, "actor": actor, "channel": channel, "started_at": _now_iso(),
                "finished_at": None, "updated_ts": time.time(), "errors": [], **self.counts,
            })
        except Exception:
            logger.warning("reindex job not stored", exc_info=True)

    def start(self, total: int) -> None:
        self.counts["total"] = total
        self._flush(force=True)

    def file_done(self, file_path: str, chunks: int, error: str | None = None) -> None:
        self.counts["processed"] += 1
        if error is not None:
            self.counts["failed"] += 1
            if len(self.errors) < _KEPT_ERRORS:
                self.errors.append({"file_path": file_path, "error": error[:300]})
            file_event(self._log, op="failed", collection=self.collection, file_path=file_path,
                       channel=self.channel, actor=self.actor, error=error, job_id=self.id)
        elif chunks == 0:
            self.counts["empty"] += 1
        else:
            self.counts["chunks"] += chunks
        self._flush()

    def finish(self, error: str | None = None) -> None:
        state = "failed" if error else "done"
        self._flush(force=True, extra={"state": state, "finished_at": _now_iso(), "error": error})

    def _flush(self, *, force: bool = False, extra: dict[str, Any] | None = None) -> None:
        now = time.monotonic()
        if self._log is None or self.id is None or (not force and now - self._flushed < _FLUSH_SECONDS):
            return
        self._flushed = now
        try:
            self._log.update(self.id, {**self.counts, "errors": self.errors, "updated_ts": time.time(), **(extra or {})})
        except Exception:
            logger.warning("reindex job %s not updated", self.id, exc_info=True)


# ---------------------------------------------------------------------------
# Read API (admin)
# ---------------------------------------------------------------------------


def _job_view(job: dict[str, Any], now: float) -> dict[str, Any]:
    stalled = job.get("state") == "running" and now - float(job.get("updated_ts") or 0) > STALLED_AFTER
    return {**job, "stalled": stalled}


@indexing_router.get("/api/indexing/summary")
def indexing_summary(request: Request, hours: int = Query(24, ge=1, le=24 * 30)):
    """Files per outcome over the last `hours`, and when each channel last sent one."""
    require_admin(principal_of(request))
    log: EventLog = request.app.state.index_events
    since = time.time() - hours * 3600
    counts = {op: log.count({"kind": "file", "op": op}, since=since) for op in ("indexed", "deleted", "skipped", "failed")}
    channels = []
    for channel in CHANNELS:
        last, _ = log.query({"kind": "file", "channel": channel}, limit=1)
        if last:
            channels.append({"channel": channel, "last_at": last[0]["at"], "last_ts": last[0]["ts"],
                             "last_file": last[0].get("file_path")})
    return {"hours": hours, "counts": counts, "channels": channels, "retention_days": log.retention_days}


@indexing_router.get("/api/indexing/jobs")
def indexing_jobs(request: Request, limit: int = Query(20, ge=1, le=100)):
    require_admin(principal_of(request))
    items, _ = request.app.state.index_events.query({"kind": "job"}, limit=limit)
    now = time.time()
    return {"items": [_job_view(j, now) for j in items]}


@indexing_router.get("/api/indexing/events")
def indexing_events(
    request: Request,
    collection: str | None = None,
    channel: str | None = None,
    op: str | None = None,
    directory: str | None = Query(None, description="Only files under this directory (stored path)"),
    before: float | None = None,
    limit: int = Query(50, ge=1, le=200),
):
    require_admin(principal_of(request))
    folder = (directory.rstrip("/") + "/") if directory else None
    items, next_before = request.app.state.index_events.query(
        {"kind": "file", "collection": collection, "channel": channel, "op": op, "path_dirs": folder},
        before=before, limit=limit,
    )
    for item in items:
        item.pop("path_dirs", None)
    return {"items": items, "next_before": next_before}
