"""Bearer-key authentication for deployed instances (MNEMOS_AUTH_ENABLED=true).

Tenants live in the `tenants` config document; every request under /api,
/internal or /mcp must carry `Authorization: Bearer <key>` whose SHA-256 is one
tenant's `key_sha256`. /health and the dashboard's static files stay open so
probes and the login prompt still load.

The middleware resolves the key to a `Principal` (see `server.principal`) from
an in-memory table, replaced whole by the config sync when the document moves:
no database call per request, revocation effective within one poll.
"""
from __future__ import annotations

import hashlib
import json
import re
import secrets
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import yaml

from server.principal import PRINCIPAL_SCOPE_KEY, Principal

_PROTECTED_PREFIXES = ("/api", "/internal", "/mcp")
# Shipped in config/tenants.example.yaml: never let a copied template go live.
_PLACEHOLDER_KEYS = {"CHANGE_ME"}

TENANT_NAME = re.compile(r"^[A-Za-z0-9_.-]{1,64}$")
ALL_TAGS = "*"


class TenantsConfigError(RuntimeError):
    """The tenants file cannot back authentication; the server must not start."""


def hash_key(api_key: str) -> str:
    return hashlib.sha256(api_key.encode()).hexdigest()


def generate_key() -> str:
    return secrets.token_urlsafe(32)


def parse_scope(value: object) -> str | list[str]:
    """`"*"` (every tag) or a list of tags, normalised; ValueError otherwise."""
    if value == ALL_TAGS:
        return ALL_TAGS
    if not isinstance(value, list) or not all(isinstance(t, str) for t in value):
        raise ValueError('expected "*" or a list of tags')
    return sorted({t.strip() for t in value if t.strip()})


def principal_of_tenant(name: str, entry: Mapping[str, Any]) -> Principal:
    def scope(value: object) -> frozenset[str] | None:
        parsed = parse_scope(value)
        return None if parsed == ALL_TAGS else frozenset(parsed)

    return Principal(
        name=name,
        admin=bool(entry.get("admin")),
        read=scope(entry.get("read", [])),
        write=scope(entry.get("write", [])),
    )


def load_api_keys(path: Path | str) -> dict[str, str]:
    """Return `{api_key: tenant_name}` from a tenants.yaml file (bootstrap import).

    Raises TenantsConfigError on anything that would leave auth half-working:
    missing file, no tenant, empty or placeholder key, key shared by two tenants.
    """
    path = Path(path)
    if not path.is_file():
        raise TenantsConfigError(f"tenants file not found: {path}")
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:
        raise TenantsConfigError(f"{path}: invalid YAML: {exc}") from exc

    tenants = raw.get("tenants") if isinstance(raw, dict) else None
    if not isinstance(tenants, dict) or not tenants:
        raise TenantsConfigError(f"{path}: no tenant declared under `tenants:`")

    keys: dict[str, str] = {}
    for name, entry in tenants.items():
        key = entry.get("api_key") if isinstance(entry, dict) else None
        if not isinstance(key, str) or not key.strip() or key in _PLACEHOLDER_KEYS:
            raise TenantsConfigError(f"{path}: tenant {name!r} has no usable api_key")
        if key in keys:
            raise TenantsConfigError(
                f"{path}: api_key shared by tenants {keys[key]!r} and {name!r}"
            )
        keys[key] = str(name)
    return keys


class TenantTable:
    """`sha256(key) → Principal`, swapped atomically by the config sync."""

    def __init__(self) -> None:
        self._by_hash: dict[str, Principal] = {}

    def replace(self, data: Mapping[str, Any]) -> None:
        """Rebuild from the `tenants` document data; one rebinding, no partial state."""
        table: dict[str, Principal] = {}
        for name, entry in (data.get("tenants") or {}).items():
            table[entry["key_sha256"]] = principal_of_tenant(name, entry)
        self._by_hash = table

    def lookup(self, api_key: str) -> Principal | None:
        return self._by_hash.get(hash_key(api_key))

    def __len__(self) -> int:
        return len(self._by_hash)


def _is_protected(path: str) -> bool:
    return any(path == p or path.startswith(p + "/") for p in _PROTECTED_PREFIXES)


class BearerAuthMiddleware:
    """Pure ASGI middleware, so it also guards the mounted /mcp app."""

    def __init__(self, app, tenants: TenantTable) -> None:
        self._app = app
        self._tenants = tenants

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or not _is_protected(scope["path"]):
            await self._app(scope, receive, send)
            return

        token = ""
        for name, value in scope.get("headers", []):
            if name == b"authorization":
                scheme, _, credentials = value.decode("latin-1").partition(" ")
                if scheme.lower() == "bearer":
                    token = credentials.strip()
                break

        principal = self._tenants.lookup(token) if token else None
        if principal is None:
            if token:
                # A key was presented and no tenant holds it: worth a trace. A
                # request with no key at all is the dashboard before sign-in.
                await _record_rejected_key(scope)
            await _unauthorized(send)
            return
        scope[PRINCIPAL_SCOPE_KEY] = principal
        await self._app(scope, receive, send)


async def _record_rejected_key(scope) -> None:
    import anyio

    from server.audit import record_auth_failure

    app = scope.get("app")
    log = getattr(getattr(app, "state", None), "audit", None)
    client = scope.get("client")
    await anyio.to_thread.run_sync(lambda: record_auth_failure(
        log, path=scope["path"], reason="unknown key", ip=client[0] if client else None,
    ))


async def _unauthorized(send) -> None:
    body = json.dumps({"detail": "Missing or invalid API key"}).encode()
    await send({
        "type": "http.response.start",
        "status": 401,
        "headers": [
            (b"content-type", b"application/json"),
            (b"content-length", str(len(body)).encode()),
            (b"www-authenticate", b"Bearer"),
        ],
    })
    await send({"type": "http.response.body", "body": body})
