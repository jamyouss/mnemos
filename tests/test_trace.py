"""Search traces (Playground): what they record, and what they cost."""
from unittest.mock import MagicMock

import pytest

from core.collections import DENSE_VECTOR_NAME, SPARSE_VECTOR_NAME
from core.reranker import ScoredDoc
from server.search import SearchService
from server.trace import RetrievalTrace


def _point(pid, score=0.5, **payload):
    p = MagicMock()
    p.id = pid
    p.score = score
    p.payload = {"content": f"content {pid}", "file_path": f"{pid}.py", **payload}
    return p


def _qdrant(fused, dense_ids, sparse_ids):
    """query_points answering the fused query and each side on its own."""
    client = MagicMock()

    def query_points(collection_name, query=None, using=None, prefetch=None, **_):
        if prefetch:
            points = fused
        elif using == DENSE_VECTOR_NAME:
            points = [_point(i) for i in dense_ids]
        elif using == SPARSE_VECTOR_NAME:
            points = [_point(i) for i in sparse_ids]
        else:
            raise AssertionError("unexpected query")
        return MagicMock(points=points)

    client.query_points.side_effect = query_points
    return client


@pytest.fixture
def embeddings():
    service = MagicMock()
    service.embed_query.return_value = [0.1] * 8
    return service


def _by_path(trace: dict) -> dict:
    return {c["file_path"]: c for c in trace["candidates"]}


def test_trace_tells_where_each_result_stood_on_each_side(embeddings):
    """The question a trace answers: did this hit come from meaning or words?"""
    qdrant = _qdrant(
        fused=[_point("a", 0.9), _point("b", 0.4)],
        dense_ids=["b", "a"],
        sparse_ids=["a"],
    )
    service = SearchService(qdrant_client=qdrant, embedding_service=embeddings)
    trace = RetrievalTrace()

    results = service.search_code("q", limit=2, trace=trace)
    report = trace.finish(results)

    a, b = _by_path(report)["a.py"], _by_path(report)["b.py"]
    assert (a["dense_rank"], a["sparse_rank"], a["fused_rank"], a["final_rank"]) == (2, 1, 1, 1)
    # Absent from the BM25 top: that is what None means, not "rank unknown".
    assert (b["dense_rank"], b["sparse_rank"], b["final_rank"]) == (1, None, 2)
    names = [s["name"] for s in report["stages"]]
    assert names[:2] == ["embed", "retrieve"]
    assert all(s["ms"] is None or s["ms"] >= 0 for s in report["stages"])


def test_without_trace_the_search_costs_one_query(embeddings):
    """The side queries are the price of a trace; normal traffic never pays it."""
    qdrant = _qdrant(fused=[_point("a")], dense_ids=["a"], sparse_ids=["a"])
    service = SearchService(qdrant_client=qdrant, embedding_service=embeddings)

    service.search_code("q")

    assert qdrant.query_points.call_count == 1


def test_trace_records_the_reranker_moving_results(embeddings):
    qdrant = _qdrant(fused=[_point("a", 0.9), _point("b", 0.4)], dense_ids=["a", "b"], sparse_ids=[])
    reranker = MagicMock()
    reranker.enabled = True
    # The cross-encoder disagrees with the fusion: b first.
    reranker.rerank.side_effect = lambda q, cands, top_k=None: [
        ScoredDoc(doc_id=1, score=2.5, payload=cands[1][1]),
        ScoredDoc(doc_id=0, score=-1.0, payload=cands[0][1]),
    ]
    service = SearchService(qdrant_client=qdrant, embedding_service=embeddings, reranker=reranker)
    trace = RetrievalTrace()

    results = service.search_code("q", limit=2, trace=trace)
    report = trace.finish(results)

    b = _by_path(report)["b.py"]
    assert (b["fused_rank"], b["rerank_rank"], b["rerank_score"], b["final_rank"]) == (2, 1, 2.5, 1)
    assert report["candidates"][0]["file_path"] == "b.py"
    assert any(s["name"] == "rerank" and s["ms"] is not None for s in report["stages"])


def test_bypassing_the_cache_neither_reads_nor_writes_it(embeddings):
    """A Playground query must show the live pipeline, and leave no trace in
    the cache that real traffic would then be served from."""
    qdrant = _qdrant(fused=[_point("a")], dense_ids=["a"], sparse_ids=[])
    cache = MagicMock()
    cache.enabled = True
    service = SearchService(qdrant_client=qdrant, embedding_service=embeddings, cache=cache)
    trace = RetrievalTrace()

    service.search("q", collections=["mnemos_code"], use_cache=False, trace=trace)

    cache.lookup.assert_not_called()
    cache.store.assert_not_called()
    assert trace.stages[0] == {"name": "cache", "ms": None, "hit": False, "skipped": "bypassed"}


def test_source_tags_the_query_log_entry(embeddings):
    qdrant = _qdrant(fused=[_point("a")], dense_ids=[], sparse_ids=[])
    logger = MagicMock()
    logger.enabled = True
    service = SearchService(qdrant_client=qdrant, embedding_service=embeddings, query_logger=logger)

    service.search_code("q", source="playground")
    assert logger.log.call_args.kwargs["extra"]["source"] == "playground"

    service.search_code("q")
    assert "source" not in logger.log.call_args.kwargs["extra"]
