"""Bearer-key authentication for deployed instances (MNEMOS_AUTH_ENABLED=true).

Keys live in `tenants.yaml`; every request under /api, /internal or /mcp must
carry `Authorization: Bearer <key>` matching one of them. /health and the
dashboard's static files stay open so probes and the login prompt still load.

Tenants are identities, not isolation: every tenant reads and writes the same
collections.
"""
from __future__ import annotations

import hmac
import json
from pathlib import Path

import yaml

_PROTECTED_PREFIXES = ("/api", "/internal", "/mcp")
# Shipped in config/tenants.example.yaml: never let a copied template go live.
_PLACEHOLDER_KEYS = {"CHANGE_ME"}


class TenantsConfigError(RuntimeError):
    """The tenants file cannot back authentication; the server must not start."""


def load_api_keys(path: Path | str) -> dict[str, str]:
    """Return `{api_key: tenant_name}` from a tenants.yaml file.

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


def _is_protected(path: str) -> bool:
    return any(path == p or path.startswith(p + "/") for p in _PROTECTED_PREFIXES)


class BearerAuthMiddleware:
    """Pure ASGI middleware, so it also guards the mounted /mcp app."""

    def __init__(self, app, api_keys: dict[str, str]) -> None:
        self._app = app
        self._keys = [key.encode() for key in api_keys]

    def _is_known(self, token: str) -> bool:
        # Compare against every key: timing must not reveal which one came close.
        candidate = token.encode()
        matches = [hmac.compare_digest(key, candidate) for key in self._keys]
        return any(matches)

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

        if not token or not self._is_known(token):
            await _unauthorized(send)
            return
        await self._app(scope, receive, send)


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
