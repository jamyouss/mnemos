"""Server configuration documents, stored in Qdrant so every pod shares them.

One point per key in the vectorless `CONFIG_COLLECTION`, payload
`{key, data, revision, updated_at}`. `revision` starts at 1 and grows by one
per write; 0 stands for an absent key.

Qdrant has no compare-and-swap: `put` checks the revision then writes, which
leaves a millisecond window where two pods saving the same key both pass the
check and the last one wins. Acceptable for human admin edits; a lock guards
the window within one process.
"""
from __future__ import annotations

import logging
import threading
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from qdrant_client.models import PointStruct

from core.collections import CONFIG_COLLECTION

logger = logging.getLogger("mnemos.config_store")

# `connectors_sync` holds the dashboard's "sync now" requests and how far the
# sync service has picked them up, `connectors_status` what it last reported
# per source: shared state, not configuration.
CONFIG_KEYS = ("connectors", "connectors_status", "connectors_sync", "projects", "settings", "tenants")


@dataclass(frozen=True)
class ConfigDoc:
    key: str
    data: dict
    revision: int
    updated_at: str


class RevisionConflict(Exception):
    """The caller edited `key` from a revision that is no longer the current one."""

    def __init__(self, key: str, current: int) -> None:
        super().__init__(f"{key} changed meanwhile (now at revision {current}): reload and retry")
        self.key = key
        self.current = current


def config_point_id(key: str) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"mnemos-config:{key}"))


def _check_key(key: str) -> None:
    if key not in CONFIG_KEYS:
        raise ValueError(f"unknown config key {key!r}")


class ConfigDocStore:
    def __init__(self, qdrant_client: Any) -> None:
        self._qdrant = qdrant_client
        self._lock = threading.Lock()

    def ensure_collection(self) -> None:
        if self._qdrant.collection_exists(CONFIG_COLLECTION):
            return
        try:
            self._qdrant.create_collection(collection_name=CONFIG_COLLECTION, vectors_config={})
        except Exception:
            # Another pod starting at the same time created it first.
            if not self._qdrant.collection_exists(CONFIG_COLLECTION):
                raise

    def get(self, key: str) -> ConfigDoc | None:
        _check_key(key)
        records = self._qdrant.retrieve(
            collection_name=CONFIG_COLLECTION,
            ids=[config_point_id(key)],
            with_payload=True,
            with_vectors=False,
        )
        if not records:
            return None
        payload = records[0].payload or {}
        return ConfigDoc(
            key=key,
            data=payload.get("data") or {},
            revision=int(payload.get("revision", 0)),
            updated_at=str(payload.get("updated_at", "")),
        )

    def revisions(self) -> dict[str, int]:
        """Current revision of every stored key, in one round trip."""
        records = self._qdrant.retrieve(
            collection_name=CONFIG_COLLECTION,
            ids=[config_point_id(key) for key in CONFIG_KEYS],
            with_payload=["key", "revision"],
            with_vectors=False,
        )
        out: dict[str, int] = {}
        for record in records:
            payload = record.payload or {}
            if payload.get("key") in CONFIG_KEYS:
                out[payload["key"]] = int(payload.get("revision", 0))
        return out

    def put(self, key: str, data: dict, expected_revision: int | None = None) -> ConfigDoc:
        """Write `data` as the next revision of `key`.

        `expected_revision=None` writes unconditionally; otherwise it must be
        the current revision (0 for an absent key) or RevisionConflict is raised.
        """
        _check_key(key)
        with self._lock:
            current = self.get(key)
            current_revision = current.revision if current else 0
            if expected_revision is not None and expected_revision != current_revision:
                raise RevisionConflict(key, current_revision)
            doc = ConfigDoc(
                key=key,
                data=data,
                revision=current_revision + 1,
                updated_at=datetime.now(UTC).isoformat(timespec="seconds"),
            )
            self._qdrant.upsert(
                collection_name=CONFIG_COLLECTION,
                points=[PointStruct(
                    id=config_point_id(key),
                    vector={},
                    payload={
                        "key": key,
                        "data": data,
                        "revision": doc.revision,
                        "updated_at": doc.updated_at,
                    },
                )],
                wait=True,
            )
        logger.info("Config %s saved at revision %d", key, doc.revision)
        return doc

    def create_if_absent(self, key: str, data: dict) -> ConfigDoc:
        """Store `data` as revision 1 unless `key` exists; return the stored doc."""
        existing = self.get(key)
        if existing is not None:
            return existing
        try:
            return self.put(key, data, expected_revision=0)
        except RevisionConflict:
            # Another pod bootstrapped the same key meanwhile: theirs stands.
            return self.get(key)  # type: ignore[return-value]
