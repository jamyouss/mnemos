"""Embedding migration against an in-memory Qdrant: aliases are real here."""
from __future__ import annotations

import pytest
from qdrant_client import QdrantClient
from qdrant_client.models import PointStruct

from core.collections import DENSE_VECTOR_NAME, SPARSE_VECTOR_NAME
from core.embedding_migration import physical_name, switch, sync
from core.indexer import Indexer, create_hybrid_collection, dense_vector_size
from core.sparse import bm25_sparse


class _Embedder:
    """3-d vectors; records the framed inputs it was asked to embed."""

    def __init__(self, model_name: str = "acme/new-model", dimension: int = 3):
        self.model_name = model_name
        self.dimension = dimension
        self.seen: list[tuple[str, str | None]] = []

    def embed_documents(self, texts, titles=None):
        self.seen += list(zip(texts, titles or [None] * len(texts)))
        return [[1.0] + [0.0] * (self.dimension - 1) for _ in texts]

    def embed_query(self, text, task="search"):
        return [1.0] + [0.0] * (self.dimension - 1)


def _point(pid: int, content: str, path: str, stamp: str = "t0") -> PointStruct:
    return PointStruct(
        id=pid,
        vector={DENSE_VECTOR_NAME: [0.5, 0.5], SPARSE_VECTOR_NAME: bm25_sparse(content)},
        payload={"content": content, "file_path": path, "last_indexed_at": stamp, "tags": ["p"]},
    )


@pytest.fixture
def qdrant():
    q = QdrantClient(":memory:")
    create_hybrid_collection(q, "mnemos_code", 2, tagged=True)
    q.upsert("mnemos_code", points=[
        _point(1, "func A()", "/data/codebase/svc/a.go"),
        _point(2, "func B()", "/data/codebase/svc/b.go"),
    ])
    return q


def test_sync_reembeds_into_a_model_named_copy_keeping_payload_and_sparse(qdrant):
    emb = _Embedder()
    sync(qdrant, "mnemos_code", emb)

    target = physical_name("mnemos_code", "acme/new-model", 3)
    assert target == "mnemos_code__new-model_3"
    assert dense_vector_size(qdrant, target) == 3
    copied = {p.id: p for p in qdrant.retrieve(target, ids=[1, 2], with_payload=True, with_vectors=True)}
    assert copied[1].payload["file_path"] == "/data/codebase/svc/a.go"
    assert copied[1].vector[SPARSE_VECTOR_NAME].indices  # BM25 side carried over
    # Titles are codebase-relative, as the indexer frames them.
    assert sorted(emb.seen) == [("func A()", "svc/a.go"), ("func B()", "svc/b.go")]
    # The served collection is untouched until the switch.
    assert dense_vector_size(qdrant, "mnemos_code") == 2


def test_second_sync_only_touches_what_changed(qdrant):
    sync(qdrant, "mnemos_code", _Embedder())
    qdrant.upsert("mnemos_code", points=[
        _point(2, "func B2()", "/data/codebase/svc/b.go", stamp="t1"),   # edited
        _point(3, "func C()", "/data/codebase/svc/c.go"),                # added
    ])
    qdrant.delete("mnemos_code", points_selector=[1])                     # removed

    emb = _Embedder()
    counts = sync(qdrant, "mnemos_code", emb)

    assert counts == {"reembedded": 2, "deleted": 1}
    assert sorted(t for t, _ in emb.seen) == ["func B2()", "func C()"]
    target = physical_name("mnemos_code", "acme/new-model", 3)
    assert sorted(p.id for p in qdrant.scroll(target, limit=10)[0]) == [2, 3]


def test_memories_are_embedded_untitled_like_the_deduplicator_does():
    q = QdrantClient(":memory:")
    create_hybrid_collection(q, "mnemos_memory", 2, tagged=True)
    q.upsert("mnemos_memory", points=[_point(1, "use flat routes", "memory/abc")])
    emb = _Embedder()
    sync(q, "mnemos_memory", emb)
    assert emb.seen == [("use flat routes", None)]


def test_switch_serves_the_logical_name_from_the_copy(qdrant):
    sync(qdrant, "mnemos_code", _Embedder())
    switch(qdrant, "mnemos_code", "acme/new-model", snapshot=False)

    assert dense_vector_size(qdrant, "mnemos_code") == 3
    assert [(a.alias_name, a.collection_name) for a in qdrant.get_aliases().aliases] == [
        ("mnemos_code", "mnemos_code__new-model_3")
    ]
    hits = qdrant.query_points("mnemos_code", query=[1.0, 0.0, 0.0], using=DENSE_VECTOR_NAME).points
    assert {h.id for h in hits} == {1, 2}


def test_server_startup_and_recreate_respect_the_alias(qdrant):
    sync(qdrant, "mnemos_code", _Embedder())
    switch(qdrant, "mnemos_code", "acme/new-model", snapshot=False)
    indexer = Indexer(qdrant_client=qdrant, embedding_service=_Embedder())

    indexer.ensure_collection("mnemos_code")  # must not try to create over the alias
    indexer.recreate_collection_hybrid("mnemos_code")

    assert qdrant.count("mnemos_code").count == 0
    assert [a.collection_name for a in qdrant.get_aliases().aliases] == ["mnemos_code__new-model_3"]


def test_switch_refuses_an_incomplete_copy(qdrant):
    sync(qdrant, "mnemos_code", _Embedder())
    qdrant.upsert("mnemos_code", points=[_point(3, "func C()", "/data/codebase/svc/c.go")])
    with pytest.raises(RuntimeError, match="run sync again"):
        switch(qdrant, "mnemos_code", "acme/new-model", snapshot=False)
    assert qdrant.collection_exists("mnemos_code")
    assert not qdrant.get_aliases().aliases


def test_switching_between_two_copies_moves_the_alias(qdrant):
    sync(qdrant, "mnemos_code", _Embedder())
    switch(qdrant, "mnemos_code", "acme/new-model", snapshot=False)
    sync(qdrant, "mnemos_code", _Embedder("acme/other", dimension=5))

    switch(qdrant, "mnemos_code", "acme/other", snapshot=False)
    assert dense_vector_size(qdrant, "mnemos_code") == 5
    switch(qdrant, "mnemos_code", "acme/new-model", snapshot=False)
    assert dense_vector_size(qdrant, "mnemos_code") == 3
