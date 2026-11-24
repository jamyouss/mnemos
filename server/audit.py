"""Who did what: the audit trail of the dashboard's Activity page.

Every change to tenants, projects, settings, connectors, the index and the
memories is recorded once it succeeded, with a diff of what changed; every
such change refused with 403 is recorded too. Entries go to the
`mnemos_audit` collection (shared by every pod, pruned after
MNEMOS_AUDIT_RETENTION_DAYS) and, as one JSON line, to the `mnemos.audit`
logger for a log pipeline.

Recording never fails a request: a write the database refuses is logged and
the request goes on. Secrets never reach an entry: tenant keys are not in any
diff, secret settings are masked.
"""
from __future__ import annotations

import functools
import json
import logging
import re
import threading
import time
from datetime import UTC, datetime
from typing import Any

import anyio

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.exception_handlers import http_exception_handler

from core.event_log import EventLog
from server.principal import principal_of, require_admin

logger = logging.getLogger("mnemos.audit")
audit_router = APIRouter()

AUDIT_COLLECTION = "mnemos_audit"
AUDIT_FIELDS = ("actor", "action", "domain", "outcome", "via", "target")

# Clients name themselves with this header; anything else is "api".
CLIENT_HEADER = "x-mnemos-client"
CLIENTS = ("dashboard", "cli", "watcher", "hook", "connector", "script")

MASK = "••••"

# Credentials that may ride in a URL: `https://user:token@host`, `?token=…`.
_URL_USERINFO = re.compile(r"(?<=://)[^/\s@]+@")
_URL_SECRET_PARAM = re.compile(
    r"([?&](?:access_token|api[_-]?key|key|password|secret|sig|signature|token)=)[^&#\s]+", re.IGNORECASE,
)

# Repeats of one refusal (same caller, action and target) within this window
# are recorded once: a loop of forbidden calls must not bury the trail.
DENIAL_WINDOW = 60.0
_denials: dict[tuple, float] = {}
_denials_lock = threading.Lock()

# Entries this process could not store; the Activity page surfaces them.
_failures = {"count": 0, "last_at": None}
_failures_lock = threading.Lock()

# Audited routes, so a 403 can be recorded under the action it refused.
# Keys are (method, route path as declared).
ROUTE_ACTIONS: dict[tuple[str, str], str] = {
    ("POST", "/api/tenants"): "tenant.create",
    ("PATCH", "/api/tenants/{name}"): "tenant.update",
    ("POST", "/api/tenants/{name}/rotate"): "tenant.rotate",
    ("DELETE", "/api/tenants/{name}"): "tenant.delete",
    ("PUT", "/api/projects"): "projects.update",
    ("PATCH", "/api/config"): "config.update",
    ("DELETE", "/api/config/{key}"): "config.reset",
    ("PUT", "/api/connectors"): "connectors.update",
    ("POST", "/api/connectors/sync"): "connectors.sync",
    ("POST", "/api/reindex"): "index.reindex",
    ("DELETE", "/api/index/{collection}/{file_path:path}"): "index.delete",
    ("POST", "/api/memory"): "memory.create",
    ("POST", "/api/memory/{mem_id}/review"): "memory.review",
    ("POST", "/api/memory/extract"): "memory.extract",
}


def client_of(request: Request | None) -> str:
    """Which client sent the request: its X-Mnemos-Client header, else mcp or api."""
    if request is None:
        return "api"
    claimed = (request.headers.get(CLIENT_HEADER) or "").strip().lower()
    if claimed in CLIENTS:
        return claimed
    return "mcp" if request.url.path.startswith("/mcp") else "api"


def client_ip(request: Request | None) -> str | None:
    """The peer address. Behind a proxy, run uvicorn with --proxy-headers and
    FORWARDED_ALLOW_IPS so it resolves X-Forwarded-For from trusted hops only."""
    if request is None or request.client is None:
        return None
    return request.client.host


def redact(value: Any) -> Any:
    """`value` with URL credentials masked, recursively through dicts and lists."""
    if isinstance(value, str):
        return _URL_SECRET_PARAM.sub(rf"\g<1>{MASK}", _URL_USERINFO.sub(f"{MASK}@", value))
    if isinstance(value, dict):
        return {k: redact(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [redact(v) for v in value]
    return value


def first_denial(key: tuple, now: float | None = None) -> bool:
    """True when `key` was not refused within DENIAL_WINDOW: record it; else skip the repeat."""
    now = time.monotonic() if now is None else now
    with _denials_lock:
        if now - _denials.get(key, -DENIAL_WINDOW - 1) <= DENIAL_WINDOW:
            return False
        _denials[key] = now
        if len(_denials) > 10_000:  # bounded: forget the oldest half
            for old in sorted(_denials, key=_denials.get)[:5_000]:
                del _denials[old]
        return True


def write_failures() -> dict[str, Any]:
    with _failures_lock:
        return {"write_failures": _failures["count"], "last_write_failure_at": _failures["last_at"]}


def record(
    log: EventLog | None,
    *,
    actor: str,
    action: str,
    target: str = "",
    changes: dict[str, Any] | None = None,
    outcome: str = "ok",
    via: str = "api",
    ip: str | None = None,
    detail: str | None = None,
    wait: bool = True,
) -> None:
    entry = {
        "actor": actor,
        "action": action,
        "domain": action.split(".", 1)[0],
        "target": redact(target),
        "outcome": outcome,
        "via": via,
        "ip": ip,
        "changes": redact(changes or {}),
        "detail": redact(detail),
    }
    logger.info(json.dumps(entry, ensure_ascii=False, default=str))
    if log is None:
        return
    try:
        log.record(entry, wait=wait)
    except Exception:
        with _failures_lock:
            _failures["count"] += 1
            _failures["last_at"] = datetime.now(UTC).isoformat(timespec="seconds")
        logger.exception("audit entry not stored: %r %r by %r", action, target, actor)


def audit(
    request: Request,
    action: str,
    target: str = "",
    changes: dict[str, Any] | None = None,
    *,
    outcome: str = "ok",
    detail: str | None = None,
    wait: bool = True,
) -> None:
    """Record `action` by the caller of `request`."""
    record(
        getattr(request.app.state, "audit", None),
        actor=principal_of(request).name,
        action=action,
        target=target,
        changes=changes,
        outcome=outcome,
        via=client_of(request),
        ip=client_ip(request),
        detail=detail,
        wait=wait,
    )


async def audit_refusals(request: Request, exc: HTTPException):
    """Exception handler: a 403 on an audited route is recorded, then answered as usual."""
    if exc.status_code == 403:
        route = request.scope.get("route")
        action = ROUTE_ACTIONS.get((request.method, getattr(route, "path", "")))
        if action:
            params = request.path_params
            target = "/".join(str(v) for v in params.values()) if params else ""
            if first_denial((principal_of(request).name, action, target)):
                # Off the event loop: the write is a blocking Qdrant call.
                await anyio.to_thread.run_sync(functools.partial(
                    audit, request, action, target, outcome="denied", detail=str(exc.detail), wait=False,
                ))
    return await http_exception_handler(request, exc)


def record_auth_failure(log: EventLog | None, *, path: str, reason: str, ip: str | None) -> None:
    """A request refused with 401: no key, or one no tenant holds. Never records the key."""
    if first_denial(("auth", ip, reason)):
        record(log, actor="anonymous", action="auth.failed", target=path, outcome="denied",
               via="api", ip=ip, detail=reason, wait=False)


# ---------------------------------------------------------------------------
# Diffs
# ---------------------------------------------------------------------------


def field_changes(before: dict[str, Any], after: dict[str, Any], keys: list[str] | None = None) -> dict[str, list]:
    """`{field: [old, new]}` for every field that differs."""
    fields = keys if keys is not None else sorted(set(before) | set(after))
    return {k: [before.get(k), after.get(k)] for k in fields if before.get(k) != after.get(k)}


def keyed_changes(before: dict[str, dict], after: dict[str, dict]) -> dict[str, Any]:
    """What happened to a set of named entries: added, removed, changed (per field)."""
    out: dict[str, Any] = {}
    added = sorted(set(after) - set(before))
    removed = sorted(set(before) - set(after))
    changed = {
        name: diff
        for name in sorted(set(before) & set(after))
        if (diff := field_changes(before[name], after[name]))
    }
    if added:
        out["added"] = added
    if removed:
        out["removed"] = removed
    if changed:
        out["changed"] = changed
    return out


# ---------------------------------------------------------------------------
# Read API (admin)
# ---------------------------------------------------------------------------


@audit_router.get("/api/audit")
def list_audit(
    request: Request,
    actor: str | None = None,
    domain: str | None = None,
    action: str | None = None,
    outcome: str | None = None,
    target: str | None = None,
    since: float | None = None,
    before: float | None = Query(None, description="Cursor: the next_before of the previous page"),
    limit: int = Query(50, ge=1, le=200),
):
    require_admin(principal_of(request))
    log: EventLog = request.app.state.audit
    items, next_before = log.query(
        {"actor": actor, "domain": domain, "action": action, "outcome": outcome, "target": target},
        since=since, before=before, limit=limit,
    )
    return {
        "items": items,
        "next_before": next_before,
        "actors": log.values("actor"),
        "actions": log.values("action"),
        "retention_days": log.retention_days,
        **write_failures(),
    }
