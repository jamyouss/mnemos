"""REST surface of the dashboard's Connectors page: /api/connectors.

The server never runs a sync. It edits the connectors file, which
`mnemos ingest serve` re-reads when it changes. Tokens stay in the ingester's
environment: only the names of their variables (`token_env`) pass through
here, and nothing outside the known source keys is ever sent back.
"""
from __future__ import annotations

import contextlib
import os
import tempfile
from pathlib import Path
from typing import Any

import yaml
from connectors.config import SOURCE_KEYS, ConfigError, parse_config, read_config_file
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from server.config import settings
from server.config_api import writable_store

connectors_router = APIRouter()

# Order the keys are written in, so a saved file reads like the example.
_KEY_ORDER = (
    "type", "name", "url", "user", "token_env", "deployment", "branch",
    "spaces", "projects", "jql", "urls", "max_pages",
    "paths", "exclude", "max_file_kb", "collection", "tags",
)

_HEADER = """\
# Knowledge connectors, read by `mnemos ingest serve` (see docs/CONNECTORS.md).
# Written by the mnemos dashboard: hand edits are fine, but saving from the
# Connectors page rewrites this file and drops comments.
# Tokens never go here: a source that needs one names the variable holding
# it (`token_env`), set in the ingester's environment.

"""


class ConnectorsPutRequest(BaseModel):
    interval: Any = None
    sources: list[Any]


def _path() -> Path:
    return Path(settings.mnemos_connectors_config)


def _public_source(entry: object) -> dict | None:
    """Known keys only: a stray `token:` written by hand must not reach the browser."""
    if not isinstance(entry, dict) or entry.get("type") not in SOURCE_KEYS:
        return None
    allowed = SOURCE_KEYS[entry["type"]]
    return {k: v for k, v in entry.items() if k in allowed}


def _snapshot(request: Request) -> dict:
    path = _path()
    body: dict[str, Any] = {
        "path": str(path),
        "writable": request.app.state.config_store.writable,
        "exists": path.is_file(),
        "interval": None,
        "sources": [],
        "problems": [],
    }
    if not body["exists"]:
        return body
    try:
        raw = read_config_file(path)
    except ConfigError as exc:
        body["problems"] = exc.problems
        return body
    if raw.get("interval") is not None:
        body["interval"] = str(raw["interval"])
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


def _write_atomic(path: Path, text: str) -> None:
    # Through a symlink (a dotfiles repo), write the target, not over the link.
    target = path.resolve() if path.is_symlink() and path.exists() else path
    fd, tmp = tempfile.mkstemp(dir=target.parent, prefix=f".{target.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
        # mkstemp makes it 0600; the ingester may run as another user. No secret in it.
        os.chmod(tmp, 0o644)
        os.replace(tmp, target)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp)
        raise


@connectors_router.get("/api/connectors")
def get_connectors(request: Request):
    return _snapshot(request)


@connectors_router.put("/api/connectors")
def put_connectors(body: ConnectorsPutRequest, request: Request):
    writable_store(request)
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

    path = _path()
    text = _HEADER + yaml.safe_dump(raw, sort_keys=False, allow_unicode=True, default_flow_style=None)
    try:
        _write_atomic(path, text)
    except OSError as exc:
        raise HTTPException(
            status_code=500, detail=f"Could not write {path}: {exc.strerror or exc}"
        ) from exc
    return _snapshot(request)
