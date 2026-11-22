"""Dashboard-editable settings: the overrides file, precedence and live apply.

Precedence is override > env var > default. Overrides live in
`<mnemos_state_dir>/settings.json` as `{key: value}`; only keys the registry
marks editable are honoured. They are applied when the app is built, so a
restart keeps them, and live on PATCH for `apply: live` keys.
"""
from __future__ import annotations

import json
import logging
import os
import tempfile
import threading
from pathlib import Path
from typing import Any

from pydantic_settings import BaseSettings

from server.settings_registry import FIELDS, FIELDS_BY_KEY, SECTIONS, FieldSpec, coerce

logger = logging.getLogger("mnemos.settings")

OVERRIDES_FILENAME = "settings.json"


class ConfigValidationError(ValueError):
    """A PATCH rejected as a whole; `errors` maps each bad key to its reason."""

    def __init__(self, errors: dict[str, str]) -> None:
        super().__init__("invalid settings: " + ", ".join(sorted(errors)))
        self.errors = errors


def _read_overrides(path: Path) -> dict[str, Any]:
    """Valid editable overrides from `path`; anything else is logged and dropped.

    The server must boot whatever the file holds, so nothing here raises.
    """
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return {}
    except OSError as exc:
        logger.warning("Ignoring settings overrides %s: unreadable (%s)", path, exc)
        return {}
    try:
        data = json.loads(raw)
    except ValueError as exc:
        logger.warning("Ignoring settings overrides %s: invalid JSON (%s)", path, exc)
        return {}
    if not isinstance(data, dict):
        logger.warning("Ignoring settings overrides %s: expected a JSON object", path)
        return {}

    clean: dict[str, Any] = {}
    for key, value in data.items():
        spec = FIELDS_BY_KEY.get(key)
        if spec is None or not spec.editable:
            logger.warning("Ignoring %s in %s: not an editable setting", key, path)
            continue
        try:
            clean[key] = coerce(spec, value)
        except ValueError as exc:
            logger.warning("Ignoring %s in %s: %s", key, path, exc)
    return clean


def _write_atomic(path: Path, data: dict[str, Any]) -> None:
    """Write via a temp file in the same dir + os.replace: never a torn file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=2, sort_keys=True)
            fh.write("\n")
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


class ConfigStore:
    """Owns the overrides of one `Settings` instance for the process lifetime."""

    def __init__(self, settings: BaseSettings, path: Path) -> None:
        self._settings = settings
        self._path = path
        self._lock = threading.Lock()
        # Env/default values, before any override: what DELETE reverts to.
        self._base = {f.key: getattr(settings, f.key) for f in FIELDS}
        self._overrides = _read_overrides(path)
        for key, value in self._overrides.items():
            setattr(settings, key, value)
        # What the running components were built with: a restart key whose
        # configured value differs from this one waits for a restart.
        self._boot = {f.key: getattr(settings, f.key) for f in FIELDS}

    @classmethod
    def load(cls, settings: BaseSettings) -> ConfigStore:
        """Read `<state_dir>/settings.json` and apply it to `settings`."""
        return cls(settings, Path(settings.mnemos_state_dir) / OVERRIDES_FILENAME)

    @property
    def path(self) -> Path:
        return self._path

    @property
    def writable(self) -> bool:
        return bool(self._settings.mnemos_ui_config_writable)

    # ------------------------------------------------------------------
    # Read
    # ------------------------------------------------------------------

    def _configured(self, key: str) -> Any:
        return self._overrides[key] if key in self._overrides else self._base[key]

    def _source(self, spec: FieldSpec) -> str:
        if spec.key in self._overrides:
            return "override"
        # pydantic-settings matches env vars case-insensitively.
        if any(name.upper() == spec.env for name in os.environ):
            return "env"
        return "default"

    def pending_restart(self) -> list[str]:
        return [
            f.key for f in FIELDS
            if f.editable and f.apply != "live" and self._configured(f.key) != self._boot[f.key]
        ]

    def _field(self, spec: FieldSpec) -> dict[str, Any]:
        secret = spec.type == "secret"
        value = self._configured(spec.key)
        return {
            "key": spec.key,
            "env": spec.env,
            "label": spec.label,
            "description": spec.description,
            "type": spec.type,
            "options": list(spec.options) if spec.options else None,
            "min": spec.min,
            "max": spec.max,
            # A secret's value never leaves the process, not even partially.
            "value": None if secret else value,
            "default": None if secret else type(self._settings).model_fields[spec.key].default,
            "source": self._source(spec),
            "editable": spec.editable,
            "apply": spec.apply,
            "is_set": bool(value) if secret else value not in (None, ""),
        }

    def snapshot(self) -> dict[str, Any]:
        """The GET /api/config body."""
        with self._lock:
            return {
                "writable": self.writable,
                "overrides_path": str(self._path),
                "pending_restart": self.pending_restart(),
                "sections": [
                    {
                        "id": section_id,
                        "label": label,
                        "fields": [self._field(f) for f in FIELDS if f.section == section_id],
                    }
                    for section_id, label in SECTIONS
                ],
            }

    # ------------------------------------------------------------------
    # Write
    # ------------------------------------------------------------------

    def _apply_live(self, spec: FieldSpec, value: Any, state: Any) -> None:
        setattr(self._settings, spec.key, value)
        if spec.live_target is None:
            return
        component_name, attr = spec.live_target
        component = getattr(state, component_name, None)
        if component is not None:
            setattr(component, attr, value)

    def update(self, values: dict[str, Any], state: Any) -> list[str]:
        """Validate every key, persist, then apply the live ones.

        All-or-nothing: one bad key rejects the whole request before anything
        is written. Returns the live keys applied, in request order.
        """
        errors: dict[str, str] = {}
        coerced: dict[str, Any] = {}
        for key, value in values.items():
            spec = FIELDS_BY_KEY.get(key)
            if spec is None:
                errors[key] = "unknown setting"
            elif not spec.editable:
                errors[key] = "not editable from the dashboard"
            else:
                try:
                    coerced[key] = coerce(spec, value)
                except ValueError as exc:
                    errors[key] = str(exc)
        if not values:
            errors["values"] = "no setting given"
        if errors:
            raise ConfigValidationError(errors)

        with self._lock:
            new_overrides = {**self._overrides, **coerced}
            _write_atomic(self._path, new_overrides)
            self._overrides = new_overrides
            applied = []
            for key, value in coerced.items():
                spec = FIELDS_BY_KEY[key]
                if spec.apply == "live":
                    self._apply_live(spec, value, state)
                    applied.append(key)
        logger.info("Settings overrides saved to %s: %s", self._path, ", ".join(coerced))
        return applied

    def reset(self, key: str, state: Any) -> None:
        """Drop the override of `key`; KeyError when there is none."""
        with self._lock:
            if key not in self._overrides:
                raise KeyError(key)
            new_overrides = {k: v for k, v in self._overrides.items() if k != key}
            _write_atomic(self._path, new_overrides)
            self._overrides = new_overrides
            spec = FIELDS_BY_KEY[key]
            if spec.apply == "live":
                self._apply_live(spec, self._base[key], state)
        logger.info("Settings override %s removed from %s", key, self._path)
