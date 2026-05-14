from __future__ import annotations

import math

import pytest

from mnemos_eval.metrics import (
    aggregate_metrics,
    hit_rate_at_k,
    mrr,
    ndcg_at_k,
    precision_at_k,
    recall_at_k,
)
from mnemos_eval.schema import GoldenItem, QueryResult


def test_recall_at_k_full_match():
    assert recall_at_k(["a", "b", "c"], ["a", "b"], k=5) == 1.0


def test_recall_at_k_partial():
    assert recall_at_k(["a", "x", "y"], ["a", "b"], k=5) == 0.5


def test_recall_at_k_truncates_to_k():
    # b is outside top-2 → recall@2 sees only [a, x] → only "a" matches
    assert recall_at_k(["a", "x", "b"], ["a", "b"], k=2) == 0.5


def test_recall_at_k_empty_expected():
    assert recall_at_k(["a"], [], k=5) == 0.0


def test_precision_at_k():
    assert precision_at_k(["a", "x", "y"], ["a", "b"], k=3) == pytest.approx(1 / 3)


def test_precision_at_k_zero_results():
    assert precision_at_k([], ["a"], k=5) == 0.0


def test_mrr_first_hit():
    assert mrr(["a", "b"], ["a"]) == 1.0


def test_mrr_second_hit():
    assert mrr(["x", "a"], ["a"]) == 0.5


def test_mrr_no_hit():
    assert mrr(["x", "y"], ["a"]) == 0.0


def test_hit_rate_at_k():
    assert hit_rate_at_k(["x", "a"], ["a"], k=5) == 1.0
    assert hit_rate_at_k(["x", "y"], ["a"], k=5) == 0.0


def test_ndcg_at_k_perfect_order():
    # Perfect ordering: first result is expected, all relevance grades equal
    score = ndcg_at_k(["a", "b", "c"], ["a"], k=5)
    assert score == pytest.approx(1.0)


def test_ndcg_at_k_lower_when_relevant_is_lower_ranked():
    high = ndcg_at_k(["a", "b"], ["a"], k=5)
    low = ndcg_at_k(["x", "a"], ["a"], k=5)
    assert low < high
    # Position 2 with binary relevance: 1/log2(3)
    assert low == pytest.approx(1.0 / math.log2(3))


def test_ndcg_at_k_with_graded_relevance():
    grades = {"a": 3, "b": 1}
    # Both retrieved in correct order: ideal DCG and actual DCG are identical → 1.0
    score = ndcg_at_k(["a", "b"], ["a", "b"], k=5, relevance_grades=grades)
    assert score == pytest.approx(1.0)


def test_aggregate_metrics_groups_by_intent():
    items = [
        GoldenItem(id="q1", query="code", intent="code_search", expected_files=["f1.py"]),
        GoldenItem(id="q2", query="doc", intent="doc_lookup", expected_files=["d1.md"]),
        GoldenItem(id="q3", query="code2", intent="code_search", expected_files=["f2.py"]),
    ]
    results = [
        QueryResult(item_id="q1", retrieved_files=["f1.py", "x"], retrieved_collections=[], retrieved_scores=[], latency_ms=10.0),
        QueryResult(item_id="q2", retrieved_files=["x", "d1.md"], retrieved_collections=[], retrieved_scores=[], latency_ms=20.0),
        QueryResult(item_id="q3", retrieved_files=["x", "y"], retrieved_collections=[], retrieved_scores=[], latency_ms=30.0),
    ]

    report = aggregate_metrics(items, results, tag="test")

    assert report.n_questions == 3
    assert report.latency_p50_ms == pytest.approx(20.0)
    # 3 intents in by_intent (code_search, doc_lookup)
    intents = {row.intent: row for row in report.by_intent}
    assert "code_search" in intents
    assert "doc_lookup" in intents
    # code_search: q1 mrr=1.0, q3 mrr=0.0 → avg 0.5
    assert intents["code_search"].mrr == pytest.approx(0.5)
    # doc_lookup: q2 mrr=0.5
    assert intents["doc_lookup"].mrr == pytest.approx(0.5)


def test_aggregate_metrics_ignores_orphan_results():
    items = [GoldenItem(id="q1", query="x", intent="general", expected_files=["a"])]
    results = [
        QueryResult(item_id="q1", retrieved_files=["a"], retrieved_collections=[], retrieved_scores=[], latency_ms=1.0),
        QueryResult(item_id="unknown", retrieved_files=["a"], retrieved_collections=[], retrieved_scores=[], latency_ms=1.0),
    ]
    report = aggregate_metrics(items, results, tag="t")
    assert report.n_questions == 1
