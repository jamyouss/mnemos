"""In-memory stand-ins for the shared configuration database.

`InMemoryConfigQdrant` implements the few Qdrant calls `ConfigDocStore` makes,
so the real store code runs against it. `fake_qdrant` wraps it in a MagicMock
that also answers what the lifespan asks of every other collection, so
`create_app()` boots for real (`with TestClient(app)`). Two apps given the same
backend behave like two pods sharing one Qdrant.
"""
from __future__ import annotations

import copy
from types import SimpleNamespace
from unittest.mock import MagicMock

from core.collections import COLLECTIONS, CONFIG_COLLECTION
from core.config_store import ConfigDocStore


class InMemoryConfigQdrant:
    def __init__(self) -> None:
        self.collections: set[str] = set()
        self.points: dict[str, dict] = {}
        self.upserts = 0

    def collection_exists(self, name: str) -> bool:
        return name in self.collections

    def create_collection(self, collection_name: str, vectors_config: dict) -> None:
        assert vectors_config == {}
        self.collections.add(collection_name)

    def retrieve(self, collection_name, ids, with_payload=True, with_vectors=False):
        assert collection_name in self.collections
        out = []
        for point_id in ids:
            if point_id not in self.points:
                continue
            payload = copy.deepcopy(self.points[point_id])
            if isinstance(with_payload, list):
                payload = {k: v for k, v in payload.items() if k in with_payload}
            out.append(SimpleNamespace(id=point_id, payload=payload))
        return out

    def upsert(self, collection_name, points, wait=True) -> None:
        assert collection_name in self.collections
        for point in points:
            assert point.vector == {}
            self.points[str(point.id)] = copy.deepcopy(point.payload)
        self.upserts += 1


def fake_qdrant(backend: InMemoryConfigQdrant, dimension: int = 384) -> MagicMock:
    """A Qdrant client: real config calls on `backend`, mocks for the rest."""
    client = MagicMock()

    def collection_exists(name: str) -> bool:
        if name == CONFIG_COLLECTION:
            return backend.collection_exists(name)
        return name in {c.name for c in COLLECTIONS}

    def create_collection(collection_name: str, **kwargs) -> None:
        if collection_name == CONFIG_COLLECTION:
            backend.create_collection(collection_name, **kwargs)

    def retrieve(collection_name: str, ids, **kwargs):
        return backend.retrieve(collection_name, ids, **kwargs) if collection_name == CONFIG_COLLECTION else []

    def upsert(collection_name: str, points, **kwargs) -> None:
        if collection_name == CONFIG_COLLECTION:
            backend.upsert(collection_name, points, **kwargs)

    client.collection_exists.side_effect = collection_exists
    client.create_collection.side_effect = create_collection
    client.retrieve.side_effect = retrieve
    client.upsert.side_effect = upsert
    client.get_collection.return_value.config.params.vectors = {
        "dense": SimpleNamespace(size=dimension),
    }
    client.get_aliases.return_value.aliases = []
    client.get_collections.return_value.collections = []
    return client


def doc_store(backend: InMemoryConfigQdrant | None = None) -> ConfigDocStore:
    store = ConfigDocStore(backend or InMemoryConfigQdrant())
    store.ensure_collection()
    return store


def isolate_settings(monkeypatch, tmp_path, **overrides) -> None:
    """Restore every setting after the test; point the import files at nothing.

    The poll interval is an hour: tests drive `config_sync.poll_once()`
    themselves instead of racing a thread.
    """
    from server.config import settings
    from server.settings_registry import FIELDS

    for f in FIELDS:
        monkeypatch.setattr(settings, f.key, getattr(settings, f.key))
    values = {
        "mnemos_state_dir": str(tmp_path / "state"),
        "mnemos_query_log_path": str(tmp_path / "logs" / "q.jsonl"),
        "mnemos_connectors_config": str(tmp_path / "absent" / "connectors.yaml"),
        "mnemos_projects_config_path": str(tmp_path / "absent" / "projects.yaml"),
        "mnemos_tenants_path": str(tmp_path / "absent" / "tenants.yaml"),
        "mnemos_ui_path": str(tmp_path / "absent" / "ui"),
        "mnemos_bootstrap_admin_key": "",
        "mnemos_auth_enabled": False,
        "mnemos_ui_config_writable": True,
        "mnemos_config_poll_seconds": 3600.0,
        **overrides,
    }
    for key, value in values.items():
        monkeypatch.setattr(settings, key, value)


def boot(monkeypatch, backend: InMemoryConfigQdrant):
    """A TestClient over `create_app()`; use it as a context manager to run the lifespan."""
    from fastapi.testclient import TestClient

    monkeypatch.setattr("server.main.QdrantClient", lambda **kw: fake_qdrant(backend))
    monkeypatch.setattr(
        "server.main.EmbeddingService", lambda **kw: MagicMock(dimension=384),
    )
    from server.main import create_app
    return TestClient(create_app())
