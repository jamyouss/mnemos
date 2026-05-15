from __future__ import annotations

import json
import time
from types import SimpleNamespace
from unittest.mock import MagicMock

from rag_core.cache import SemanticCache


def _mock_embed_service(vec=None):
    mock = MagicMock()
    mock.embed.return_value = vec or [0.1] * 384
    return mock


def _mock_qdrant():
    mock = MagicMock()
    # collection bootstrap: "doesn't exist yet"
    mock.get_collections.return_value = SimpleNamespace(collections=[])
    return mock


def test_cache_disabled_lookup_returns_none():
    c = SemanticCache(_mock_qdrant(), _mock_embed_service(), enabled=False)
    assert c.lookup("anything") is None


def test_cache_disabled_store_is_noop():
    qdrant = _mock_qdrant()
    c = SemanticCache(qdrant, _mock_embed_service(), enabled=False)
    c.store("q", [{"a": 1}])
    qdrant.upsert.assert_not_called()


def test_cache_lookup_miss_when_no_points():
    qdrant = _mock_qdrant()
    qdrant.query_points.return_value = SimpleNamespace(points=[])
    c = SemanticCache(qdrant, _mock_embed_service(), enabled=True)
    assert c.lookup("q") is None


def test_cache_lookup_miss_when_score_below_threshold():
    qdrant = _mock_qdrant()
    qdrant.query_points.return_value = SimpleNamespace(
        points=[
            SimpleNamespace(
                id="x",
                score=0.8,
                payload={
                    "query": "q",
                    "namespace": "default",
                    "results": "[]",
                    "created_at": time.time(),
                },
            )
        ]
    )
    c = SemanticCache(qdrant, _mock_embed_service(), enabled=True, threshold=0.95)
    assert c.lookup("q") is None


def test_cache_lookup_hit():
    qdrant = _mock_qdrant()
    qdrant.query_points.return_value = SimpleNamespace(
        points=[
            SimpleNamespace(
                id="x",
                score=0.98,
                payload={
                    "query": "original",
                    "namespace": "default",
                    "results": json.dumps([{"file_path": "a.go", "score": 0.9}]),
                    "created_at": time.time(),
                },
            )
        ]
    )
    c = SemanticCache(qdrant, _mock_embed_service(), enabled=True, threshold=0.95)
    hit = c.lookup("similar query")
    assert hit is not None
    assert hit.query == "original"
    assert hit.payload == [{"file_path": "a.go", "score": 0.9}]


def test_cache_lookup_expires_old_entries():
    qdrant = _mock_qdrant()
    qdrant.query_points.return_value = SimpleNamespace(
        points=[
            SimpleNamespace(
                id="x",
                score=0.99,
                payload={
                    "query": "q",
                    "namespace": "default",
                    "results": "[]",
                    "created_at": time.time() - 10_000,  # well past TTL
                },
            )
        ]
    )
    c = SemanticCache(qdrant, _mock_embed_service(), enabled=True, ttl_seconds=60)
    assert c.lookup("q") is None
    qdrant.delete.assert_called_once()


def test_cache_lookup_respects_namespace():
    qdrant = _mock_qdrant()
    qdrant.query_points.return_value = SimpleNamespace(
        points=[
            SimpleNamespace(
                id="x",
                score=0.99,
                payload={
                    "query": "q",
                    "namespace": "code",
                    "results": "[]",
                    "created_at": time.time(),
                },
            )
        ]
    )
    c = SemanticCache(qdrant, _mock_embed_service(), enabled=True)
    # Different namespace → miss, even though the vector matches.
    assert c.lookup("q", namespace="skills") is None


def test_cache_store_upserts_with_payload():
    qdrant = _mock_qdrant()
    c = SemanticCache(qdrant, _mock_embed_service(), enabled=True)
    c.store("q", [{"file_path": "a.go", "score": 1.0}])
    args, kwargs = qdrant.upsert.call_args
    assert kwargs["collection_name"] == "mnemos_cache"
    point = kwargs["points"][0]
    assert point.payload["query"] == "q"
    assert json.loads(point.payload["results"]) == [{"file_path": "a.go", "score": 1.0}]


def test_cache_invalidate_recreates_collection():
    qdrant = _mock_qdrant()
    c = SemanticCache(qdrant, _mock_embed_service(), enabled=True)
    qdrant.create_collection.reset_mock()
    c.invalidate()
    qdrant.delete_collection.assert_called_once()
    qdrant.create_collection.assert_called_once()
