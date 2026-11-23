"""First-start import of the configuration files into the database.

Each config key absent from the database is filled once from its historical
file: connectors.yaml, projects.yaml, `<state_dir>/settings.json`,
tenants.yaml. A key whose file does not exist stays absent, so a file supplied
later is still imported on the next start. Once a key is stored, the database
is the only source of truth and the file is never read again.

Tenants imported from tenants.yaml become admins with every tag: that is the
power every key had before scopes existed. `MNEMOS_BOOTSTRAP_ADMIN_KEY` makes
tenant `admin` the root tenant, checked at every start: the dashboard can
neither edit, rotate nor delete it, and changing the variable rotates its key.
"""
from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from connectors.config import SOURCE_KEYS, ConfigError, read_config_file
from core.config_store import ConfigDocStore, RevisionConflict
from core.projects import read_projects_file
from pydantic_settings import BaseSettings

from server.auth import ALL_TAGS, TenantsConfigError, hash_key, load_api_keys
from server.settings_store import SETTINGS_KEY, clean_overrides

logger = logging.getLogger("mnemos.config_bootstrap")

OVERRIDES_FILENAME = "settings.json"
ROOT_TENANT = "admin"


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def admin_tenant(api_key: str) -> dict[str, Any]:
    return {
        "key_sha256": hash_key(api_key),
        "admin": True,
        "read": ALL_TAGS,
        "write": ALL_TAGS,
        "created_at": _now(),
    }


def sync_root_tenant(docs: ConfigDocStore, api_key: str) -> None:
    """Store tenant `admin` as the root, keyed by `api_key`; a no-op when already so."""
    for _ in range(3):
        doc = docs.get("tenants")
        tenants = dict((doc.data.get("tenants") if doc else None) or {})
        current = tenants.get(ROOT_TENANT) or {}
        wanted = {
            **admin_tenant(api_key),
            "root": True,
            "created_at": current.get("created_at") or _now(),
        }
        if current == wanted:
            return
        tenants[ROOT_TENANT] = wanted
        try:
            docs.put("tenants", {"tenants": tenants}, expected_revision=doc.revision if doc else 0)
        except RevisionConflict:
            continue  # another pod starting at the same time wrote first: re-check
        logger.info("Root tenant %s set from MNEMOS_BOOTSTRAP_ADMIN_KEY", ROOT_TENANT)
        return
    logger.warning("Root tenant not synced: the tenants kept changing during start-up")


def _connectors_from_file(path: Path) -> dict | None:
    if not path.is_file():
        return None
    try:
        raw = read_config_file(path)
    except ConfigError as exc:
        logger.warning("Not importing %s: %s", path, exc)
        return None
    data: dict[str, Any] = {}
    if raw.get("interval") is not None:
        data["interval"] = raw["interval"]
    sources = []
    for index, entry in enumerate(raw.get("sources") if isinstance(raw.get("sources"), list) else []):
        kind = entry.get("type") if isinstance(entry, dict) else None
        allowed = SOURCE_KEYS.get(kind) if isinstance(kind, str) else None
        if allowed is None:
            logger.warning("Not importing %s sources[%d]: unknown source type", path, index)
            continue
        # A token pasted into the file must not reach the database.
        dropped = sorted(k for k in entry if k not in allowed)
        if dropped:
            logger.warning("Not importing %s sources[%d] keys: %s", path, index, ", ".join(dropped))
        sources.append({k: v for k, v in entry.items() if k in allowed})
    data["sources"] = sources
    return data


def _projects_from_file(path: Path) -> dict | None:
    if not path.is_file():
        return None
    paths = read_projects_file(path).get("paths")
    if not isinstance(paths, dict):
        logger.warning("Not importing %s: no `paths:` mapping", path)
        return None
    return {"paths": {str(prefix): value for prefix, value in paths.items()}}


def _settings_from_file(path: Path) -> dict | None:
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    except OSError as exc:
        logger.warning("Not importing %s: unreadable (%s)", path, exc)
        return None
    try:
        data = json.loads(raw)
    except ValueError as exc:
        logger.warning("Not importing %s: invalid JSON (%s)", path, exc)
        return None
    return clean_overrides(data, str(path))


def _tenants_from_file(path: Path, auth_enabled: bool) -> dict | None:
    if not path.is_file():
        return None
    try:
        keys = load_api_keys(path)
    except TenantsConfigError:
        if auth_enabled:
            raise
        logger.warning("Not importing %s: unusable tenants file", path, exc_info=True)
        return None
    return {"tenants": {name: admin_tenant(key) for key, name in keys.items()}}


def bootstrap_config(docs: ConfigDocStore, settings: BaseSettings) -> None:
    """Import every absent key from its file; refuse an authenticated server without tenants."""
    present = docs.revisions()
    imports = {
        "connectors": lambda: _connectors_from_file(Path(settings.mnemos_connectors_config)),
        "projects": lambda: _projects_from_file(Path(settings.mnemos_projects_config_path)),
        SETTINGS_KEY: lambda: _settings_from_file(
            Path(settings.mnemos_state_dir) / OVERRIDES_FILENAME
        ),
        "tenants": lambda: _tenants_from_file(
            Path(settings.mnemos_tenants_path), settings.mnemos_auth_enabled
        ),
    }
    for key, read in imports.items():
        if key in present:
            continue
        data = read()
        if data is None:
            continue
        doc = docs.create_if_absent(key, data)
        logger.info("Imported %s into the database (revision %d)", key, doc.revision)
    if settings.mnemos_bootstrap_admin_key:
        sync_root_tenant(docs, settings.mnemos_bootstrap_admin_key)

    if settings.mnemos_auth_enabled:
        tenants = docs.get("tenants")
        if tenants is None or not tenants.data.get("tenants"):
            raise TenantsConfigError(
                "MNEMOS_AUTH_ENABLED=true but the database holds no tenant: provide "
                f"{settings.mnemos_tenants_path} or MNEMOS_BOOTSTRAP_ADMIN_KEY for the first start"
            )
