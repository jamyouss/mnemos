"""Operational state the dashboard surfaces on every page.

- `POST /api/services/heartbeat`: a background service (the watcher) says it
  is alive; the sync service already does through `/api/connectors/sync/report`.
- `TenantUsage`: when each API key was last used, flushed to the shared
  `tenant_usage` document every minute so every pod contributes.
- `GET /api/alerts`: what is broken right now, for the banner above every page.
"""
from __future__ import annotations

import logging
import threading
import time
from datetime import UTC, datetime
from typing import Any, Literal

from fastapi import APIRouter, Request
from pydantic import BaseModel

from core.config_store import ConfigDocStore
from server.api import health_deps
from server.audit import write_failures
from server.connectors_api import _source_names, _sync_state, _update
from server.principal import principal_of, require_write_all

logger = logging.getLogger("mnemos.ops")
ops_router = APIRouter()

SERVICES_KEY = "services_status"
USAGE_KEY = "tenant_usage"
# A service beats every minute: three silent minutes mean it stopped.
SILENT_AFTER = 180.0


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _age(iso: str | None) -> float | None:
    return time.time() - datetime.fromisoformat(iso).timestamp() if iso else None


class HeartbeatBody(BaseModel):
    name: Literal["watcher"]


@ops_router.post("/api/services/heartbeat")
def heartbeat(body: HeartbeatBody, request: Request):
    require_write_all(principal_of(request))
    _update(request.app.state.config_docs, SERVICES_KEY,
            lambda data: {**data, body.name: {"seen_at": _now()}})
    return {"name": body.name, "seen_at": _now()}


class TenantUsage:
    """Last use of each tenant's key: noted in memory per request, merged into
    the shared document every `every` seconds (latest wins per tenant)."""

    def __init__(self, docs: ConfigDocStore, every: float = 60.0) -> None:
        self._docs = docs
        self._every = every
        self._pending: dict[str, dict[str, Any]] = {}
        self._lock = threading.Lock()
        self._stop = threading.Event()

    def touch(self, name: str, ip: str | None) -> None:
        with self._lock:
            self._pending[name] = {"last_used_at": _now(), "last_used_ip": ip}

    def flush(self) -> None:
        with self._lock:
            pending, self._pending = self._pending, {}
        if not pending:
            return

        def merge(data: dict) -> dict:
            merged = dict(data)
            for name, use in pending.items():
                if (merged.get(name) or {}).get("last_used_at", "") <= use["last_used_at"]:
                    merged[name] = use
            return merged

        try:
            _update(self._docs, USAGE_KEY, merge)
        except Exception:
            logger.warning("tenant usage not stored", exc_info=True)
            with self._lock:  # kept for the next flush, newer uses winning
                self._pending = {**pending, **self._pending}

    def read(self) -> dict[str, dict[str, Any]]:
        """Stored uses, overlaid with this pod's not yet flushed ones."""
        doc = self._docs.get(USAGE_KEY)
        stored = dict(doc.data) if doc else {}
        with self._lock:
            return {**stored, **self._pending}

    def run(self) -> None:
        while not self._stop.wait(self._every):
            self.flush()

    def stop(self) -> None:
        self._stop.set()
        self.flush()


def _alert(id_: str, severity: str, title: str, detail: str, href: str | None) -> dict[str, Any]:
    return {"id": id_, "severity": severity, "title": title, "detail": detail, "href": href}


@ops_router.get("/api/alerts")
def alerts(request: Request):
    """What needs attention now, limited to what the caller may see and act on."""
    principal = principal_of(request)
    state = request.app.state
    out: list[dict[str, Any]] = []

    deps = health_deps(request)
    if not deps["llm"]["ok"]:
        out.append(_alert("llm-down", "warning", "The LLM is unreachable",
                          f"{deps['llm']['provider']}: {deps['llm']['error'] or 'no answer'}. Memory extraction, "
                          "and any enabled grader, rewriter or contextual chunking, fail meanwhile.", "/config"))

    if principal.admin or principal.write_all:
        docs = state.config_docs
        if names := set(_source_names(docs)):
            sync = _sync_state(docs)
            silent = _age(sync["service_seen_at"])
            if silent is None:
                out.append(_alert("sync-never", "warning", "No sync service has reported",
                                  "Connectors are configured but nothing syncs them: start `mnemos ingest serve`.",
                                  "/connectors"))
            elif silent > SILENT_AFTER:
                out.append(_alert("sync-silent", "error", "The sync service stopped reporting",
                                  f"Last heard from {int(silent // 60)} min ago; connector sources are not synced.",
                                  "/connectors"))
            # A removed source keeps its last report until it is pruned: not this alert's business.
            failed = sorted(name for name, run in sync["runs"].items()
                            if run.get("state") == "failed" and name in names)
            if failed:
                out.append(_alert("sync-failed", "warning", "A connector sync failed",
                                  f"Last sync failed for {', '.join(failed)}.", "/connectors"))

    if principal.admin:
        services = state.config_docs.get(SERVICES_KEY)
        watcher = (services.data if services else {}).get("watcher") or {}
        silent = _age(watcher.get("seen_at"))
        if silent is not None and silent > SILENT_AFTER:
            out.append(_alert("watcher-silent", "error", "The watcher stopped reporting",
                              f"Last heard from {int(silent // 60)} min ago: file changes are not indexed.",
                              "/indexing"))
        failures = write_failures()
        if failures["write_failures"]:
            out.append(_alert("audit-write-failures", "error", "Audit entries were lost",
                              f"{failures['write_failures']} could not be stored on this server since it started; "
                              "they are in its log (mnemos.audit).", "/activity"))
        jobs, _ = state.index_events.query({"kind": "job"}, since=time.time() - 86400, limit=50)
        # Newest first: a later run of the same reindex supersedes a failed one.
        latest: dict[tuple, dict] = {}
        for job in jobs:
            latest.setdefault((job.get("collection"), job.get("path")), job)
        broken = [j for j in latest.values() if j.get("state") in ("failed", "partial")]
        if broken:
            out.append(_alert("reindex-failed", "warning", "A reindex did not complete cleanly",
                              f"{len(broken)} reindex job(s) in the last 24 h failed or left failed files, "
                              "and were not run again since.", "/indexing"))
    return {"alerts": out}
