"""REST surface of the dashboard's Connectors page: /api/connectors.

The server never runs a sync. It stores the `connectors` config document,
which `mnemos ingest serve` reads from here. Tokens stay in the ingester's
environment: only the names of their variables (`token_env`) pass through
here, and nothing outside the known source keys is ever stored or sent back.

"Sync now" works the same way: the dashboard files a request in the
`connectors_sync` document, the ingester sees it at its next poll, queues the
sources it names and acknowledges it, which is all the dashboard can know.
"""
from __future__ import annotations

import random
import time
from datetime import UTC, datetime
from typing import Annotated, Any, Callable, Literal

from connectors.config import SOURCE_KEYS, ConfigError, parse_config
from core.config_store import ConfigDoc, ConfigDocStore, RevisionConflict
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from server.audit import audit, field_changes, keyed_changes, redact
from server.config import settings
from server.config_api import require_admin_writer, revision_conflict
from server.principal import principal_of, require_admin_or_write_all

connectors_router = APIRouter()

CONNECTORS_KEY = "connectors"
SYNC_KEY = "connectors_sync"
STATUS_KEY = "connectors_status"
# Requests kept for the ingester to catch up on; it reads every one newer than
# the last it saw, so this only has to outlast a few polls.
_KEPT_REQUESTS = 20
# Bounds on what one report may store: errors are for a human to read, not logs.
_MAX_RESULTS = 50
_MAX_ERROR = 500

# Key order of a stored source, so the document reads like the example file.
_KEY_ORDER = (
    "type", "name", "url", "user", "token_env", "deployment", "branch",
    "spaces", "projects", "jql", "urls", "max_pages",
    "paths", "exclude", "max_file_kb", "collection", "tags",
)


class ConnectorsPutRequest(BaseModel):
    interval: Any = None
    sources: list[Any]
    revision: int | None = None


class SyncRequestBody(BaseModel):
    # None: every source.
    sources: list[Annotated[str, Field(max_length=200)]] | None = Field(None, max_length=200)


class SyncAckBody(BaseModel):
    id: int = Field(ge=0)


class SyncResultBody(BaseModel):
    scope_tag: str = Field(max_length=300)
    pushed: int = 0
    unchanged: int = 0
    deleted: int = 0
    failed: int = 0
    skipped: int = 0
    truncated: bool = False
    error: str | None = None


class SyncReportBody(BaseModel):
    # No source: a heartbeat, the service is alive.
    source: str | None = Field(None, max_length=200)
    state: Literal["running", "ok", "failed"] | None = None
    results: list[SyncResultBody] = Field([], max_length=_MAX_RESULTS)


def _source_names(docs: ConfigDocStore) -> set[str]:
    """Names of the saved sources; one without a name is named after its type, as the ingester does."""
    stored = docs.get(CONNECTORS_KEY)
    entries = stored.data.get("sources") if stored and isinstance(stored.data.get("sources"), list) else []
    return {str(s.get("name") or s.get("type")) for s in entries if isinstance(s, dict)}


def _sync_state(docs: ConfigDocStore) -> dict:
    """Sync requests, how far the sync service picked them up, and what it last reported."""
    sync = docs.get(SYNC_KEY)
    status = docs.get(STATUS_KEY)
    data = sync.data if sync else {}
    reported = status.data if status else {}
    return {
        "requests": data.get("requests") if isinstance(data.get("requests"), list) else [],
        "acked_id": int(data.get("acked_id", 0)),
        "acked_at": data.get("acked_at"),
        "service_seen_at": reported.get("service_seen_at"),
        "runs": reported.get("runs") if isinstance(reported.get("runs"), dict) else {},
    }


def _update(docs: ConfigDocStore, key: str, change: Callable[[dict], dict]) -> ConfigDoc:
    """Read-modify-write `key`, retried when another pod wrote first."""
    for attempt in range(5):
        doc = docs.get(key)
        try:
            return docs.put(key, change(dict(doc.data) if doc else {}),
                            expected_revision=doc.revision if doc else 0)
        except RevisionConflict:
            # Pods and the ingester write the same document: back off a little, jittered.
            time.sleep(random.uniform(0.01, 0.05) * (attempt + 1))
    raise HTTPException(status_code=409, detail="Sync state changed too fast: try again")


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _public_source(entry: object) -> dict | None:
    """Known keys only: whatever else a source holds must not reach the browser."""
    if not isinstance(entry, dict) or entry.get("type") not in SOURCE_KEYS:
        return None
    allowed = SOURCE_KEYS[entry["type"]]
    return {k: v for k, v in entry.items() if k in allowed}


def _snapshot(doc: ConfigDoc | None) -> dict:
    raw = doc.data if doc else {}
    body: dict[str, Any] = {
        "revision": doc.revision if doc else 0,
        "writable": settings.mnemos_ui_config_writable,
        "interval": str(raw["interval"]) if raw.get("interval") is not None else None,
        "sources": [],
        "problems": [],
    }
    if doc is None:
        return body
    entries = raw.get("sources") if isinstance(raw.get("sources"), list) else []
    body["sources"] = [s for s in map(_public_source, entries) if s is not None]
    try:
        parse_config(raw, environ=None)
    except ConfigError as exc:
        body["problems"] = exc.problems
    return body


def _clean(entry: object) -> object:
    """Drop empty optional values and order the keys; anything else is left
    for `parse_config` to reject."""
    if not isinstance(entry, dict):
        return entry
    out = {k: entry[k] for k in _KEY_ORDER if entry.get(k) not in (None, "", [])}
    out.update({k: v for k, v in entry.items() if k not in _KEY_ORDER})
    return out


@connectors_router.get("/api/connectors")
def get_connectors(request: Request):
    require_admin_or_write_all(principal_of(request))
    docs = request.app.state.config_docs
    return {**_snapshot(docs.get(CONNECTORS_KEY)), "sync": _sync_state(docs)}


@connectors_router.put("/api/connectors")
def put_connectors(body: ConnectorsPutRequest, request: Request):
    require_admin_writer(request)
    raw: dict[str, Any] = {}
    interval = body.interval.strip() if isinstance(body.interval, str) else body.interval
    if interval not in (None, ""):
        raw["interval"] = interval
    raw["sources"] = [_clean(entry) for entry in body.sources]

    try:
        parse_config(raw, environ=None)
    except ConfigError as exc:
        return JSONResponse(
            status_code=422,
            content={"detail": "Invalid connectors, nothing was saved", "problems": exc.problems},
        )

    stored = request.app.state.config_docs.get(CONNECTORS_KEY)
    try:
        doc = request.app.state.config_docs.put(
            CONNECTORS_KEY, raw, expected_revision=body.revision,
        )
    except RevisionConflict as exc:
        return revision_conflict(exc)
    request.app.state.config_sync.apply(doc)
    old = stored.data if stored else {}
    changes = keyed_changes(_by_name(old.get("sources")), _by_name(raw["sources"]))
    changes.update(field_changes({"interval": old.get("interval")}, {"interval": raw.get("interval")}))
    audit(request, "connectors.update", "connectors", changes)
    return {**_snapshot(doc), "sync": _sync_state(request.app.state.config_docs)}


def _by_name(entries: object) -> dict[str, dict]:
    """Stored sources by the name the ingester gives them; token variables included, never tokens."""
    if not isinstance(entries, list):
        return {}
    return {str(e.get("name") or e.get("type")): e for e in entries if isinstance(e, dict)}


@connectors_router.get("/api/connectors/sync")
def get_sync(request: Request):
    require_admin_or_write_all(principal_of(request))
    return _sync_state(request.app.state.config_docs)


@connectors_router.post("/api/connectors/sync")
def request_sync(body: SyncRequestBody, request: Request):
    """Ask the sync service to sync now: every source, or the named ones."""
    principal = principal_of(request)
    require_admin_or_write_all(principal)
    docs = request.app.state.config_docs
    if body.sources is not None:
        unknown = sorted(set(body.sources) - _source_names(docs))
        if not body.sources or unknown:
            raise HTTPException(status_code=422, detail=f"No saved source named {', '.join(unknown) or '(none given)'}")
    sources = sorted(set(body.sources)) if body.sources is not None else None
    entry: dict[str, Any] = {}

    def add(data: dict) -> dict:
        requests = data.get("requests") if isinstance(data.get("requests"), list) else []
        acked = int(data.get("acked_id", 0))
        # The same request still waiting: answer with it rather than queue a twin.
        waiting = next((r for r in requests if int(r.get("id", 0)) > acked and r.get("sources") == sources), None)
        entry.clear()
        if waiting is not None:
            entry.update(waiting)
            return data
        last = max([acked] + [int(r.get("id", 0)) for r in requests])
        entry.update(id=last + 1, sources=sources, requested_at=_now(), requested_by=principal.name)
        return {**data, "requests": [*requests, entry][-_KEPT_REQUESTS:]}

    _update(docs, SYNC_KEY, add)
    audit(request, "connectors.sync", ", ".join(sources) if sources else "every source")
    return {"request": dict(entry), **_sync_state(docs)}


@connectors_router.post("/api/connectors/sync/ack")
def ack_sync(body: SyncAckBody, request: Request):
    """The sync service picked up every request up to `id`."""
    require_admin_or_write_all(principal_of(request))

    def ack(data: dict) -> dict:
        requests = data.get("requests") if isinstance(data.get("requests"), list) else []
        # Never past the newest request: an ack cannot mark future requests as picked up.
        newest = max([0] + [int(r.get("id", 0)) for r in requests])
        acked = min(body.id, newest)
        if acked <= int(data.get("acked_id", 0)):
            return data
        return {**data, "acked_id": acked, "acked_at": _now()}

    docs = request.app.state.config_docs
    _update(docs, SYNC_KEY, ack)
    return _sync_state(docs)


@connectors_router.post("/api/connectors/sync/report")
def report_sync(body: SyncReportBody, request: Request):
    """The sync service is alive and, with `source`, started or finished syncing it."""
    require_admin_or_write_all(principal_of(request))
    docs = request.app.state.config_docs
    known = _source_names(docs)
    if body.source and body.source not in known:
        raise HTTPException(status_code=422, detail=f"No saved source named {body.source!r}")
    now = _now()

    def record(data: dict) -> dict:
        # Sources removed from the configuration drop out of the status too.
        runs = {name: run for name, run in (data.get("runs") or {}).items() if name in known}
        if body.source and body.state:
            previous = runs.get(body.source) or {}
            running = body.state == "running"
            runs[body.source] = {
                "state": body.state,
                "started_at": now if running else previous.get("started_at"),
                # While running, what the last finished run did stays visible.
                "finished_at": previous.get("finished_at") if running else now,
                "results": previous.get("results", []) if running else [
                    {**r.model_dump(), "error": redact(r.error[:_MAX_ERROR]) if r.error else None}
                    for r in body.results
                ],
            }
        return {**data, "service_seen_at": now, "runs": runs}

    _update(docs, STATUS_KEY, record)
    return _sync_state(docs)
