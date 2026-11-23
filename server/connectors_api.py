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

from datetime import UTC, datetime
from typing import Any, Callable

from connectors.config import SOURCE_KEYS, ConfigError, parse_config
from core.config_store import ConfigDoc, ConfigDocStore, RevisionConflict
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from server.config import settings
from server.config_api import require_admin_writer, revision_conflict
from server.principal import principal_of, require_admin_or_write_all

connectors_router = APIRouter()

CONNECTORS_KEY = "connectors"
SYNC_KEY = "connectors_sync"
# Requests kept for the ingester to catch up on; it reads every one newer than
# the last it saw, so this only has to outlast a few polls.
_KEPT_REQUESTS = 20

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
    sources: list[str] | None = None


class SyncAckBody(BaseModel):
    id: int


def _sync_state(doc: ConfigDoc | None) -> dict:
    data = doc.data if doc else {}
    requests = data.get("requests") if isinstance(data.get("requests"), list) else []
    return {
        "requests": requests,
        "acked_id": int(data.get("acked_id", 0)),
        "acked_at": data.get("acked_at"),
    }


def _update_sync(docs: ConfigDocStore, change: Callable[[dict], dict]) -> ConfigDoc:
    """Read-modify-write the sync document, retried when another pod wrote first."""
    for _ in range(5):
        doc = docs.get(SYNC_KEY)
        try:
            return docs.put(SYNC_KEY, change(dict(doc.data) if doc else {}),
                            expected_revision=doc.revision if doc else 0)
        except RevisionConflict:
            continue
    raise HTTPException(status_code=409, detail="Sync requests changed too fast: try again")


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
    return {**_snapshot(docs.get(CONNECTORS_KEY)), "sync": _sync_state(docs.get(SYNC_KEY))}


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

    try:
        doc = request.app.state.config_docs.put(
            CONNECTORS_KEY, raw, expected_revision=body.revision,
        )
    except RevisionConflict as exc:
        return revision_conflict(exc)
    request.app.state.config_sync.apply(doc)
    return {**_snapshot(doc), "sync": _sync_state(request.app.state.config_docs.get(SYNC_KEY))}


@connectors_router.get("/api/connectors/sync")
def get_sync(request: Request):
    require_admin_or_write_all(principal_of(request))
    return _sync_state(request.app.state.config_docs.get(SYNC_KEY))


@connectors_router.post("/api/connectors/sync")
def request_sync(body: SyncRequestBody, request: Request):
    """Ask the sync service to sync now: every source, or the named ones."""
    principal = principal_of(request)
    require_admin_or_write_all(principal)
    docs = request.app.state.config_docs
    if body.sources is not None:
        stored = docs.get(CONNECTORS_KEY)
        entries = stored.data.get("sources") if stored and isinstance(stored.data.get("sources"), list) else []
        # A source without a name is named after its type, as the ingester does.
        known = {str(s.get("name") or s.get("type")) for s in entries if isinstance(s, dict)}
        unknown = sorted(set(body.sources) - known)
        if not body.sources or unknown:
            raise HTTPException(status_code=422, detail=f"No saved source named {', '.join(unknown) or '(none given)'}")
    entry: dict[str, Any] = {}

    def add(data: dict) -> dict:
        requests = data.get("requests") if isinstance(data.get("requests"), list) else []
        last = max([int(data.get("acked_id", 0))] + [int(r.get("id", 0)) for r in requests])
        entry.clear()
        entry.update(
            id=last + 1,
            sources=body.sources,
            requested_at=datetime.now(UTC).isoformat(timespec="seconds"),
            requested_by=principal.name,
        )
        return {**data, "requests": [*requests, entry][-_KEPT_REQUESTS:]}

    doc = _update_sync(docs, add)
    return {"request": dict(entry), **_sync_state(doc)}


@connectors_router.post("/api/connectors/sync/ack")
def ack_sync(body: SyncAckBody, request: Request):
    """The sync service picked up every request up to `id`."""
    require_admin_or_write_all(principal_of(request))

    def ack(data: dict) -> dict:
        if body.id <= int(data.get("acked_id", 0)):
            return data
        return {**data, "acked_id": body.id, "acked_at": datetime.now(UTC).isoformat(timespec="seconds")}

    return _sync_state(_update_sync(request.app.state.config_docs, ack))
