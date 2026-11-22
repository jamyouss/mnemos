"""Embedding migration against an in-memory Qdrant: aliases are real here."""
from __future__ import annotations

import pytest
from qdrant_client import QdrantClient
from qdrant_client.models import PointStruct

from core.collections import DENSE_VECTOR_NAME, SPARSE_VECTOR_NAME
from core.embedding_migration import physical_name, switch, sync
from core.indexer import Indexer, chunk_point_id, create_hybrid_collection, dense_vector_size
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


def _long_go(lines: int) -> str:
    return "\n".join(f"\tstep{i}()" for i in range(lines))


def test_rechunk_splits_long_chunks_with_indexer_ids_and_reuses_vectors(qdrant):
    # The source already runs the model: whole chunks must not be re-embedded.
    sync(qdrant, "mnemos_code", _Embedder())
    switch(qdrant, "mnemos_code", "acme/new-model", snapshot=False)
    qdrant.upsert("mnemos_code", points=[
        PointStruct(id=chunk_point_id("/data/codebase/svc/c.go", 0),
                    vector={DENSE_VECTOR_NAME: [0.0, 1.0, 0.0], SPARSE_VECTOR_NAME: bm25_sparse("x")},
                    payload={"content": _long_go(40), "file_path": "/data/codebase/svc/c.go",
                             "chunk_index": 0, "chunk_type": "function", "last_indexed_at": "t0"}),
    ])

    emb = _Embedder()
    sync(qdrant, "mnemos_code", emb, max_chunk_chars=120)

    target = physical_name("mnemos_code", "acme/new-model", 3, max_chunk_chars=120)
    parts = sorted(
        (p for p in qdrant.scroll(target, limit=100, with_payload=True)[0]
         if p.payload["file_path"].endswith("c.go")),
        key=lambda p: p.payload["chunk_index"],
    )
    assert len(parts) > 1
    assert all(len(p.payload["content"]) <= 120 for p in parts)
    assert [str(p.id) for p in parts] == [chunk_point_id("/data/codebase/svc/c.go", i) for i in range(len(parts))]
    assert {p.payload["chunk_type"] for p in parts} == {"function"}
    # Only c.go's new windows were embedded; a.go and b.go kept their vectors.
    assert {title for _, title in emb.seen} == {"svc/c.go"}


def test_rechunk_sync_follows_edited_added_and_removed_files(qdrant):
    sync(qdrant, "mnemos_code", _Embedder(), max_chunk_chars=120)
    qdrant.upsert("mnemos_code", points=[
        _point(2, _long_go(30), "/data/codebase/svc/b.go", stamp="t1"),   # edited, now long
        _point(3, "func C()", "/data/codebase/svc/c.go"),                  # added
    ])
    qdrant.delete("mnemos_code", points_selector=[1])                       # a.go removed

    emb = _Embedder()
    sync(qdrant, "mnemos_code", emb, max_chunk_chars=120)

    target = physical_name("mnemos_code", "acme/new-model", 3, max_chunk_chars=120)
    files = {p.payload["file_path"] for p in qdrant.scroll(target, limit=100, with_payload=True)[0]}
    assert files == {"/data/codebase/svc/b.go", "/data/codebase/svc/c.go"}
    assert {title for _, title in emb.seen} == {"svc/b.go", "svc/c.go"}


def test_switch_picks_the_copy_with_the_requested_chunk_size(qdrant):
    sync(qdrant, "mnemos_code", _Embedder())
    sync(qdrant, "mnemos_code", _Embedder(), max_chunk_chars=120)

    assert switch(qdrant, "mnemos_code", "acme/new-model", max_chunk_chars=120, snapshot=False) \
        == "mnemos_code__new-model_3_c120"
    assert switch(qdrant, "mnemos_code", "acme/new-model", snapshot=False) == "mnemos_code__new-model_3"


def test_memories_are_never_resplit():
    q = QdrantClient(":memory:")
    create_hybrid_collection(q, "mnemos_memory", 2, tagged=True)
    q.upsert("mnemos_memory", points=[_point(1, "x" * 500, "memory/abc")])
    assert sync(q, "mnemos_memory", _Embedder(), max_chunk_chars=120) == {"reembedded": 0, "deleted": 0}
    assert [c.name for c in q.get_collections().collections] == ["mnemos_memory"]
