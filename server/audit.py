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

import json
import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.exception_handlers import http_exception_handler

from core.event_log import EventLog
from server.principal import principal_of, require_admin

logger = logging.getLogger("mnemos.audit")
audit_router = APIRouter()

AUDIT_COLLECTION = "mnemos_audit"
AUDIT_FIELDS = ("actor", "action", "domain", "outcome", "via")

# Clients name themselves with this header; anything else is "api".
CLIENT_HEADER = "x-mnemos-client"
CLIENTS = ("dashboard", "cli", "watcher", "hook", "connector", "script")

MASK = "••••"

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
    """The caller's address; the first X-Forwarded-For hop behind a proxy (not proof, a hint)."""
    if request is None:
        return None
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else None


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
) -> None:
    entry = {
        "actor": actor,
        "action": action,
        "domain": action.split(".", 1)[0],
        "target": target,
        "outcome": outcome,
        "via": via,
        "ip": ip,
        "changes": changes or {},
        "detail": detail,
    }
    logger.info(json.dumps(entry, ensure_ascii=False, default=str))
    if log is None:
        return
    try:
        log.record(entry)
    except Exception:
        logger.exception("audit entry not stored: %s %s by %s", action, target, actor)


def audit(
    request: Request,
    action: str,
    target: str = "",
    changes: dict[str, Any] | None = None,
    *,
    outcome: str = "ok",
    detail: str | None = None,
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
    )


async def audit_refusals(request: Request, exc: HTTPException):
    """Exception handler: a 403 on an audited route is recorded, then answered as usual."""
    if exc.status_code == 403:
        route = request.scope.get("route")
        action = ROUTE_ACTIONS.get((request.method, getattr(route, "path", "")))
        if action:
            params = request.path_params
            target = "/".join(str(v) for v in params.values()) if params else ""
            audit(request, action, target, outcome="denied", detail=str(exc.detail))
    return await http_exception_handler(request, exc)


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
    since: float | None = None,
    before: float | None = Query(None, description="Cursor: the next_before of the previous page"),
    limit: int = Query(50, ge=1, le=200),
):
    require_admin(principal_of(request))
    log: EventLog = request.app.state.audit
    items, next_before = log.query(
        {"actor": actor, "domain": domain, "action": action, "outcome": outcome},
        since=since, before=before, limit=limit,
    )
    return {
        "items": items,
        "next_before": next_before,
        "actors": log.values("actor"),
        "actions": log.values("action"),
        "retention_days": log.retention_days,
    }
