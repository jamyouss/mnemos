"""REST surface of the dashboard's Connectors page: /api/connectors.

The server never runs a sync. It stores the `connectors` config document,
which `mnemos ingest serve` reads from here. Tokens stay in the ingester's
environment: only the names of their variables (`token_env`) pass through
here, and nothing outside the known source keys is ever stored or sent back.
"""
from __future__ import annotations

from typing import Any

from connectors.config import SOURCE_KEYS, ConfigError, parse_config
from core.config_store import ConfigDoc, RevisionConflict
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from server.config import settings
from server.config_api import require_admin_writer, revision_conflict
from server.principal import principal_of, require_admin_or_write_all

connectors_router = APIRouter()

CONNECTORS_KEY = "connectors"

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
    return _snapshot(request.app.state.config_docs.get(CONNECTORS_KEY))


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
    return _snapshot(doc)
