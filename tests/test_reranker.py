from __future__ import annotations

import math

from core.reranker import CrossEncoderReranker, ScoredDoc, mmr_select


def test_reranker_disabled_preserves_order():
    rr = CrossEncoderReranker(enabled=False)
    candidates = [("doc a", {"id": "a"}), ("doc b", {"id": "b"}), ("doc c", {"id": "c"})]
    out = rr.rerank("query", candidates, top_k=2)
    assert [s.payload["id"] for s in out] == ["a", "b"]
    # Synthetic scores are descending
    assert out[0].score > out[1].score


def test_reranker_disabled_empty_candidates():
    rr = CrossEncoderReranker(enabled=False)
    assert rr.rerank("q", [], top_k=5) == []


def test_mmr_select_returns_all_when_top_k_too_high():
    scored = [
        ScoredDoc(doc_id=0, score=1.0, payload="a"),
        ScoredDoc(doc_id=1, score=0.5, payload="b"),
    ]
    embeddings = [[1.0, 0.0], [0.0, 1.0]]
    out = mmr_select(scored, embeddings, top_k=10)
    assert len(out) == 2


def test_mmr_select_picks_diverse_documents():
    # Two near-identical docs + one orthogonal doc
    scored = [
        ScoredDoc(doc_id=0, score=1.0, payload="a"),
        ScoredDoc(doc_id=1, score=0.95, payload="b"),  # near-identical to a
        ScoredDoc(doc_id=2, score=0.5, payload="c"),   # diverse but lower-scored
    ]
    norm = 1.0 / math.sqrt(2)
    embeddings = [
        [norm, norm],          # a
        [norm + 1e-3, norm - 1e-3],  # b ~= a
        [norm, -norm],         # c (orthogonal-ish)
    ]
    out = mmr_select(scored, embeddings, top_k=2, lambda_=0.5)
    picks = [s.payload for s in out]
    # MMR must keep "a" (highest score) and prefer "c" over "b" (diversity).
    assert picks[0] == "a"
    assert picks[1] == "c"


def test_mmr_select_falls_back_when_embeddings_missing():
    scored = [ScoredDoc(doc_id=0, score=1.0, payload="a"), ScoredDoc(doc_id=1, score=0.5, payload="b")]
    out = mmr_select(scored, [], top_k=1)
    assert len(out) == 1
    assert out[0].payload == "a"


def test_mmr_select_empty_input():
    assert mmr_select([], [], top_k=5) == []
