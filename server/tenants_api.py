"""REST surface of the dashboard's Tenants page (/api/tenants) and /api/whoami.

A tenant is an API key with a role and tag scopes (see `server.principal`).
Only the key's SHA-256 is stored; the key itself is returned once, at creation
or rotation. At least one admin always remains, so the dashboard can never
lock itself out, and the root tenant (from MNEMOS_BOOTSTRAP_ADMIN_KEY) can be
neither edited, rotated nor deleted here.
"""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from core.config_store import ConfigDoc, RevisionConflict
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from server.auth import ALL_TAGS, TENANT_NAME, generate_key, hash_key, parse_scope
from server.config import settings
from server.audit import audit, field_changes
from server.config_api import require_admin_writer, revision_conflict
from server.principal import principal_of, require_admin

tenants_router = APIRouter()

TENANTS_KEY = "tenants"


class TenantCreateRequest(BaseModel):
    name: str
    admin: bool = False
    read: Any = []
    write: Any = []
    revision: int | None = None


class TenantPatchRequest(BaseModel):
    admin: bool | None = None
    read: Any = None
    write: Any = None
    revision: int | None = None


class RevisionRequest(BaseModel):
    revision: int | None = None


def _public(name: str, entry: dict) -> dict[str, Any]:
    return {
        "name": name,
        "admin": bool(entry.get("admin")),
        "root": bool(entry.get("root")),
        "read": entry.get("read", []),
        "write": entry.get("write", []),
        "created_at": entry.get("created_at", ""),
    }


def _scope(field: str, value: object) -> str | list[str]:
    try:
        return parse_scope(value)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=f"{field}: {exc}") from None


def _load(request: Request, revision: int | None) -> tuple[dict[str, dict], int]:
    """Stored tenants, fresh from the database, after the revision check."""
    doc = request.app.state.config_docs.get(TENANTS_KEY)
    current = doc.revision if doc else 0
    if revision is not None and revision != current:
        raise RevisionConflict(TENANTS_KEY, current)
    tenants = doc.data.get("tenants") if doc else None
    return {name: dict(entry) for name, entry in (tenants or {}).items()}, current


def _save(request: Request, tenants: dict[str, dict], current: int) -> ConfigDoc:
    if not any(entry.get("admin") for entry in tenants.values()):
        raise HTTPException(
            status_code=409, detail="At least one admin tenant must remain",
        )
    doc = request.app.state.config_docs.put(
        TENANTS_KEY, {"tenants": tenants}, expected_revision=current,
    )
    request.app.state.config_sync.apply(doc)
    return doc


def _known(tenants: dict[str, dict], name: str) -> dict:
    if name not in tenants:
        raise HTTPException(status_code=404, detail=f"No tenant {name!r}")
    return tenants[name]


def _mutable(tenants: dict[str, dict], name: str) -> dict:
    entry = _known(tenants, name)
    if entry.get("root"):
        raise HTTPException(
            status_code=403,
            detail=f"Tenant {name!r} is the root tenant: it cannot be edited, rotated or "
                   "deleted; its key comes from MNEMOS_BOOTSTRAP_ADMIN_KEY",
        )
    return entry


@tenants_router.get("/api/whoami")
def whoami(request: Request):
    principal = principal_of(request)
    return {
        "name": principal.name,
        "admin": principal.admin,
        "read": ALL_TAGS if principal.read is None else sorted(principal.read),
        "write": ALL_TAGS if principal.write is None else sorted(principal.write),
        "auth_enabled": settings.mnemos_auth_enabled,
    }


@tenants_router.get("/api/tenants")
def list_tenants(request: Request):
    require_admin(principal_of(request))
    doc = request.app.state.config_docs.get(TENANTS_KEY)
    tenants = (doc.data.get("tenants") if doc else None) or {}
    usage = request.app.state.tenant_usage.read() if getattr(request.app.state, "tenant_usage", None) else {}
    return {
        "revision": doc.revision if doc else 0,
        "writable": settings.mnemos_ui_config_writable,
        "tenants": [
            {**_public(name, tenants[name]),
             "last_used_at": (usage.get(name) or {}).get("last_used_at"),
             "last_used_ip": (usage.get(name) or {}).get("last_used_ip")}
            for name in sorted(tenants)
        ],
    }


@tenants_router.post("/api/tenants")
def create_tenant(body: TenantCreateRequest, request: Request):
    require_admin_writer(request)
    if not TENANT_NAME.match(body.name):
        raise HTTPException(
            status_code=422, detail="name: 1-64 characters among letters, digits, _ . -",
        )
    read, write = _scope("read", body.read), _scope("write", body.write)
    api_key = generate_key()
    try:
        tenants, current = _load(request, body.revision)
        if body.name in tenants:
            raise HTTPException(status_code=409, detail=f"Tenant {body.name!r} already exists")
        tenants[body.name] = {
            "key_sha256": hash_key(api_key),
            "admin": body.admin,
            "read": read,
            "write": write,
            "created_at": datetime.now(UTC).isoformat(timespec="seconds"),
        }
        doc = _save(request, tenants, current)
    except RevisionConflict as exc:
        return revision_conflict(exc)
    audit(request, "tenant.create", body.name, {"admin": body.admin, "read": read, "write": write})
    return {"tenant": _public(body.name, tenants[body.name]), "api_key": api_key,
            "revision": doc.revision}


@tenants_router.patch("/api/tenants/{name}")
def update_tenant(name: str, body: TenantPatchRequest, request: Request):
    require_admin_writer(request)
    given = body.model_fields_set
    changes: dict[str, Any] = {}
    if "admin" in given and body.admin is not None:
        changes["admin"] = body.admin
    for field in ("read", "write"):
        if field in given:
            changes[field] = _scope(field, getattr(body, field))
    try:
        tenants, current = _load(request, body.revision)
        entry = _mutable(tenants, name)
        before = dict(entry)
        entry.update(changes)
        doc = _save(request, tenants, current)
    except RevisionConflict as exc:
        return revision_conflict(exc)
    audit(request, "tenant.update", name, field_changes(before, entry, ["admin", "read", "write"]))
    return {"tenant": _public(name, tenants[name]), "revision": doc.revision}


@tenants_router.post("/api/tenants/{name}/rotate")
def rotate_tenant_key(name: str, request: Request, body: RevisionRequest | None = None):
    require_admin_writer(request)
    api_key = generate_key()
    try:
        tenants, current = _load(request, body.revision if body else None)
        _mutable(tenants, name)["key_sha256"] = hash_key(api_key)
        doc = _save(request, tenants, current)
    except RevisionConflict as exc:
        return revision_conflict(exc)
    audit(request, "tenant.rotate", name)
    return {"tenant": _public(name, tenants[name]), "api_key": api_key, "revision": doc.revision}


@tenants_router.delete("/api/tenants/{name}")
def delete_tenant(name: str, request: Request, revision: int | None = None):
    require_admin_writer(request)
    try:
        tenants, current = _load(request, revision)
        entry = _mutable(tenants, name)
        del tenants[name]
        doc = _save(request, tenants, current)
    except RevisionConflict as exc:
        return revision_conflict(exc)
    audit(request, "tenant.delete", name, {k: [entry.get(k), None] for k in ("admin", "read", "write")})
    return {"deleted": name, "revision": doc.revision}
