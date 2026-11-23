from __future__ import annotations

import os
import uuid

import pytest

from core.collections import COLLECTIONS, CONFIG_COLLECTION
from core.config_store import (
    CONFIG_KEYS,
    ConfigDocStore,
    RevisionConflict,
    config_point_id,
)
from tests.config_fakes import InMemoryConfigQdrant, doc_store


def test_config_collection_is_not_a_searchable_collection():
    assert CONFIG_COLLECTION not in {c.name for c in COLLECTIONS}


def test_ensure_collection_creates_a_vectorless_collection_once():
    backend = InMemoryConfigQdrant()
    store = ConfigDocStore(backend)
    store.ensure_collection()
    store.ensure_collection()
    assert backend.collections == {CONFIG_COLLECTION}


def test_ensure_collection_tolerates_a_pod_creating_it_concurrently():
    class Racing(InMemoryConfigQdrant):
        def create_collection(self, collection_name, vectors_config):
            self.collections.add(collection_name)
            raise RuntimeError("already exists")

    backend = Racing()
    ConfigDocStore(backend).ensure_collection()
    assert backend.collections == {CONFIG_COLLECTION}


def test_absent_key_reads_as_none_and_has_no_revision():
    store = doc_store()
    assert store.get("projects") is None
    assert store.revisions() == {}


def test_put_increments_the_revision_and_get_returns_the_data():
    store = doc_store()
    first = store.put("projects", {"paths": {"a/": ["a"]}})
    second = store.put("projects", {"paths": {"b/": ["b"]}})
    assert (first.revision, second.revision) == (1, 2)
    doc = store.get("projects")
    assert doc.data == {"paths": {"b/": ["b"]}}
    assert doc.revision == 2 and doc.updated_at
    assert store.revisions() == {"projects": 2}


def test_one_point_per_key_with_a_stable_id():
    backend = InMemoryConfigQdrant()
    store = doc_store(backend)
    for key in CONFIG_KEYS:
        store.put(key, {"k": key})
        store.put(key, {"k": key})
    assert set(backend.points) == {config_point_id(k) for k in CONFIG_KEYS}
    assert config_point_id("tenants") == str(uuid.uuid5(uuid.NAMESPACE_URL, "mnemos-config:tenants"))
    assert store.revisions() == {k: 2 for k in CONFIG_KEYS}


def test_stale_expected_revision_is_a_conflict_and_writes_nothing():
    store = doc_store()
    store.put("settings", {"mnemos_cache_enabled": True})
    with pytest.raises(RevisionConflict) as exc:
        store.put("settings", {}, expected_revision=0)
    assert exc.value.current == 1
    assert store.get("settings").data == {"mnemos_cache_enabled": True}
    assert store.put("settings", {}, expected_revision=1).revision == 2


def test_expected_revision_zero_means_absent():
    store = doc_store()
    assert store.put("tenants", {"tenants": {}}, expected_revision=0).revision == 1


def test_create_if_absent_keeps_the_existing_document():
    store = doc_store()
    created = store.create_if_absent("connectors", {"sources": []})
    again = store.create_if_absent("connectors", {"sources": ["other"]})
    assert created.revision == again.revision == 1
    assert store.get("connectors").data == {"sources": []}


def test_unknown_key_is_refused():
    with pytest.raises(ValueError):
        doc_store().put("passwords", {})


# ---------------------------------------------------------------------------
# Against a real Qdrant: set MNEMOS_TEST_QDRANT_PORT (e.g. a throwaway
# `docker run --rm -d -p 16334:6333 qdrant/qdrant`).
# ---------------------------------------------------------------------------


@pytest.mark.skipif(not os.environ.get("MNEMOS_TEST_QDRANT_PORT"), reason="no test Qdrant")
def test_round_trip_on_a_real_qdrant():
    from qdrant_client import QdrantClient

    client = QdrantClient(host="localhost", port=int(os.environ["MNEMOS_TEST_QDRANT_PORT"]))
    if client.collection_exists(CONFIG_COLLECTION):
        client.delete_collection(CONFIG_COLLECTION)
    try:
        store = ConfigDocStore(client)
        store.ensure_collection()
        store.ensure_collection()
        data = {"paths": {"Projects/acme/": {"tags": ["acme"], "exclude_dirs": ["exports"]}}}
        assert store.put("projects", data).revision == 1
        assert store.get("projects").data == data
        store.put("tenants", {"tenants": {}})
        assert store.revisions() == {"projects": 1, "tenants": 1}
        with pytest.raises(RevisionConflict):
            store.put("projects", {}, expected_revision=0)
    finally:
        client.delete_collection(CONFIG_COLLECTION)
        client.close()
