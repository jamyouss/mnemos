"""Dashboard-editable settings: the overrides document, precedence and live apply.

Precedence is override > env var > default. Overrides live in the `settings`
config document (Qdrant, shared by every pod) as `{key: value}`; only keys the
registry marks editable are honoured. They are applied when the app starts, on
PATCH/DELETE on the pod that wrote them, and by the config sync on every other
pod: `apply: live` keys reach the running components, the others wait for a
restart and show up in `pending_restart`.
"""
from __future__ import annotations

import logging
import os
import threading
from typing import Any

from core.config_store import ConfigDoc, ConfigDocStore, RevisionConflict
from pydantic_settings import BaseSettings

from server.settings_registry import FIELDS, FIELDS_BY_KEY, SECTIONS, FieldSpec, coerce

logger = logging.getLogger("mnemos.settings")

SETTINGS_KEY = "settings"


class ConfigValidationError(ValueError):
    """A PATCH rejected as a whole; `errors` maps each bad key to its reason."""

    def __init__(self, errors: dict[str, str]) -> None:
        super().__init__("invalid settings: " + ", ".join(sorted(errors)))
        self.errors = errors


def clean_overrides(data: object, origin: str) -> dict[str, Any]:
    """Valid editable overrides from `data`; anything else is logged and dropped.

    The server must run whatever the document holds, so nothing here raises.
    """
    if not isinstance(data, dict):
        logger.warning("Ignoring settings overrides from %s: expected an object", origin)
        return {}
    clean: dict[str, Any] = {}
    for key, value in data.items():
        spec = FIELDS_BY_KEY.get(key)
        if spec is None or not spec.editable:
            logger.warning("Ignoring %s in %s: not an editable setting", key, origin)
            continue
        try:
            clean[key] = coerce(spec, value)
        except ValueError as exc:
            logger.warning("Ignoring %s in %s: %s", key, origin, exc)
    return clean


class ConfigStore:
    """Owns the overrides of one `Settings` instance for the process lifetime."""

    def __init__(self, settings: BaseSettings, docs: ConfigDocStore) -> None:
        self._settings = settings
        self._docs = docs
        self._lock = threading.Lock()
        # Env/default values, before any override: what DELETE reverts to.
        self._base = {f.key: getattr(settings, f.key) for f in FIELDS}
        doc = docs.get(SETTINGS_KEY)
        self._overrides = clean_overrides(doc.data, "the settings document") if doc else {}
        self._revision = doc.revision if doc else 0
        for key, value in self._overrides.items():
            setattr(settings, key, value)
        # What the running components were built with: a restart key whose
        # configured value differs from this one waits for a restart.
        self._boot = {f.key: getattr(settings, f.key) for f in FIELDS}

    @property
    def writable(self) -> bool:
        return bool(self._settings.mnemos_ui_config_writable)

    @property
    def revision(self) -> int:
        return self._revision

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
                "revision": self._revision,
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
    # Apply
    # ------------------------------------------------------------------

    def _apply_live(self, spec: FieldSpec, value: Any, state: Any) -> None:
        setattr(self._settings, spec.key, value)
        if spec.live_target is None:
            return
        component_name, attr = spec.live_target
        component = getattr(state, component_name, None)
        if component is not None:
            setattr(component, attr, value)

    def _replace(self, overrides: dict[str, Any], revision: int, state: Any) -> list[str]:
        """Swap in `overrides` (caller holds the lock); return the live keys that changed."""
        changed = []
        for spec in FIELDS:
            if spec.apply != "live":
                continue
            before = self._configured(spec.key)
            after = overrides.get(spec.key, self._base[spec.key])
            if after != before:
                self._apply_live(spec, after, state)
                changed.append(spec.key)
        self._overrides = overrides
        self._revision = revision
        return changed

    def apply_doc(self, doc: ConfigDoc, state: Any) -> list[str]:
        """Adopt a `settings` document saved by any pod; older or equal revisions are no-ops."""
        with self._lock:
            if doc.revision <= self._revision:
                return []
            changed = self._replace(
                clean_overrides(doc.data, "the settings document"), doc.revision, state,
            )
        if changed:
            logger.info("Settings revision %d applied live: %s", doc.revision, ", ".join(changed))
        return changed

    # ------------------------------------------------------------------
    # Write
    # ------------------------------------------------------------------

    def _current(self, revision: int | None) -> tuple[dict[str, Any], int]:
        """The stored overrides, fresh from the database, after the revision check."""
        doc = self._docs.get(SETTINGS_KEY)
        current = doc.revision if doc else 0
        if revision is not None and revision != current:
            raise RevisionConflict(SETTINGS_KEY, current)
        return (clean_overrides(doc.data, "the settings document") if doc else {}), current

    def update(self, values: dict[str, Any], state: Any, revision: int | None = None) -> list[str]:
        """Validate every key, persist, then apply.

        All-or-nothing: one bad key rejects the whole request before anything
        is written. `revision`, when given, must be the stored one
        (RevisionConflict otherwise). Returns the live keys of the request, in
        request order.
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
            stored, current = self._current(revision)
            # Merged onto the stored document, not this pod's copy: another
            # pod's save not polled yet must survive.
            new_overrides = {**stored, **coerced}
            doc = self._docs.put(SETTINGS_KEY, new_overrides, expected_revision=current)
            self._replace(new_overrides, doc.revision, state)
        logger.info("Settings overrides saved: %s", ", ".join(coerced))
        return [key for key in coerced if FIELDS_BY_KEY[key].apply == "live"]

    def reset(self, key: str, state: Any, revision: int | None = None) -> None:
        """Drop the override of `key`; KeyError when there is none."""
        with self._lock:
            stored, current = self._current(revision)
            if key not in stored:
                raise KeyError(key)
            new_overrides = {k: v for k, v in stored.items() if k != key}
            doc = self._docs.put(SETTINGS_KEY, new_overrides, expected_revision=current)
            self._replace(new_overrides, doc.revision, state)
        logger.info("Settings override %s removed", key)
