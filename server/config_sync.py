"""Propagation of the shared configuration across pods.

Every pod polls `ConfigDocStore.revisions()` (one Qdrant round trip) every
`MNEMOS_CONFIG_POLL_SECONDS`. A key whose revision moved past the one this pod
applied is loaded and handed to its registered applier. The pod that saves a
document applies it at once through `apply`, so its own writes never wait for
a tick. A failed poll or applier is logged and retried on the next tick; the
last good configuration stays in force meanwhile.
"""
from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from typing import Any

from core.config_store import ConfigDoc, ConfigDocStore
from core.projects import path_excludes_from, path_tags_from

logger = logging.getLogger("mnemos.config_sync")

Applier = Callable[[ConfigDoc], None]


class ConfigSync:
    def __init__(self, docs: ConfigDocStore, interval_seconds: float) -> None:
        self._docs = docs
        self._interval = interval_seconds
        self._appliers: dict[str, Applier] = {}
        self._applied: dict[str, int] = {}
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def register(self, key: str, applier: Applier | None, revision: int) -> None:
        """`applier` runs on every newer revision of `key`; `revision` is the
        one the running components were built from. None: nothing to apply
        server-side, the revision is only tracked."""
        with self._lock:
            if applier is not None:
                self._appliers[key] = applier
            self._applied[key] = revision

    def applied_revision(self, key: str) -> int:
        with self._lock:
            return self._applied.get(key, 0)

    def apply(self, doc: ConfigDoc) -> None:
        """Apply `doc` unless this pod already runs that revision or a newer one."""
        with self._lock:
            if doc.revision <= self._applied.get(doc.key, 0):
                return
            applier = self._appliers.get(doc.key)
            if applier is not None:
                applier(doc)
            self._applied[doc.key] = doc.revision
        logger.info("Config %s revision %d applied", doc.key, doc.revision)

    def poll_once(self) -> None:
        for key, revision in self._docs.revisions().items():
            # Unregistered keys (sync requests, sync status) are shared state the
            # API reads on demand: nothing to apply, and they change too often to log.
            if key not in self._applied or revision <= self.applied_revision(key):
                continue
            doc = self._docs.get(key)
            if doc is not None:
                self.apply(doc)

    def _run(self) -> None:
        while not self._stop.wait(self._interval):
            try:
                self.poll_once()
            except Exception:
                logger.warning("Config poll failed; keeping the last applied config", exc_info=True)

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, name="mnemos-config-sync", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=self._interval + 5)
            self._thread = None


def apply_projects(state: Any, data: dict) -> None:
    """Point the indexer (and the app state the API reads) at a `projects` document."""
    path_tags = path_tags_from(data, origin="the projects document")
    path_excludes = path_excludes_from(data)
    state.path_tags = path_tags
    state.path_excludes = path_excludes
    indexer = getattr(state, "indexer", None)
    if indexer is not None:
        indexer.set_project_rules(path_tags, path_excludes)
