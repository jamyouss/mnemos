from __future__ import annotations

import math
from collections import defaultdict

from eval.schema import (
    GoldenItem,
    IntentMetrics,
    MetricsReport,
    QueryResult,
)


def recall_at_k(retrieved: list[str], expected: list[str], k: int) -> float:
    if not expected:
        return 0.0
    top_k = retrieved[:k]
    hits = len(set(top_k) & set(expected))
    return hits / len(expected)


def precision_at_k(retrieved: list[str], expected: list[str], k: int) -> float:
    if k <= 0:
        return 0.0
    top_k = retrieved[:k]
    if not top_k:
        return 0.0
    hits = len(set(top_k) & set(expected))
    return hits / min(k, len(top_k))


def mrr(retrieved: list[str], expected: list[str]) -> float:
    expected_set = set(expected)
    for rank, doc in enumerate(retrieved, start=1):
        if doc in expected_set:
            return 1.0 / rank
    return 0.0


def hit_rate_at_k(retrieved: list[str], expected: list[str], k: int) -> float:
    if not expected:
        return 0.0
    top_k = set(retrieved[:k])
    return 1.0 if top_k & set(expected) else 0.0


def ndcg_at_k(
    retrieved: list[str],
    expected: list[str],
    k: int,
    relevance_grades: dict[str, int] | None = None,
) -> float:
    if not expected:
        return 0.0

    grades = relevance_grades or {}

    def gain(doc: str) -> float:
        if doc in grades:
            return float(grades[doc])
        return 1.0 if doc in expected else 0.0

    # Deduplicate retrieved keeping first-seen rank — multiple chunks from
    # the same file otherwise produce DCG > IDCG.
    seen: set[str] = set()
    dedup_retrieved: list[str] = []
    for doc in retrieved:
        if doc not in seen:
            seen.add(doc)
            dedup_retrieved.append(doc)

    dcg = 0.0
    for i, doc in enumerate(dedup_retrieved[:k]):
        rel = gain(doc)
        if rel > 0:
            dcg += (math.pow(2, rel) - 1) / math.log2(i + 2)

    ideal_gains = sorted(
        (grades.get(f, 1) for f in expected),
        reverse=True,
    )
    idcg = 0.0
    for i, rel in enumerate(ideal_gains[:k]):
        idcg += (math.pow(2, rel) - 1) / math.log2(i + 2)

    return dcg / idcg if idcg > 0 else 0.0


def _percentile(values: list[float], p: float) -> float:
    if not values:
        return 0.0
    sorted_v = sorted(values)
    index = max(0, min(len(sorted_v) - 1, int(round(p * (len(sorted_v) - 1)))))
    return sorted_v[index]


def aggregate_metrics(
    items: list[GoldenItem],
    results: list[QueryResult],
    tag: str,
) -> MetricsReport:
    by_id = {item.id: item for item in items}

    groups: dict[str, list[tuple[GoldenItem, QueryResult]]] = defaultdict(list)
    all_pairs: list[tuple[GoldenItem, QueryResult]] = []
    latencies: list[float] = []

    for result in results:
        item = by_id.get(result.item_id)
        if item is None:
            continue
        groups[item.intent].append((item, result))
        all_pairs.append((item, result))
        latencies.append(result.latency_ms)

    def compute(pairs: list[tuple[GoldenItem, QueryResult]], intent: str) -> IntentMetrics:
        if not pairs:
            return IntentMetrics(
                intent=intent,  # type: ignore[arg-type]
                n_questions=0,
                mrr=0.0,
                ndcg_at_5=0.0,
                recall_at_1=0.0,
                recall_at_3=0.0,
                recall_at_5=0.0,
                recall_at_10=0.0,
                precision_at_5=0.0,
                hit_rate_at_5=0.0,
            )
        n = len(pairs)
        return IntentMetrics(
            intent=intent,  # type: ignore[arg-type]
            n_questions=n,
            mrr=sum(mrr(r.retrieved_files, i.expected_files) for i, r in pairs) / n,
            ndcg_at_5=sum(
                ndcg_at_k(r.retrieved_files, i.expected_files, 5, i.relevance_grades)
                for i, r in pairs
            ) / n,
            recall_at_1=sum(recall_at_k(r.retrieved_files, i.expected_files, 1) for i, r in pairs) / n,
            recall_at_3=sum(recall_at_k(r.retrieved_files, i.expected_files, 3) for i, r in pairs) / n,
            recall_at_5=sum(recall_at_k(r.retrieved_files, i.expected_files, 5) for i, r in pairs) / n,
            recall_at_10=sum(recall_at_k(r.retrieved_files, i.expected_files, 10) for i, r in pairs) / n,
            precision_at_5=sum(precision_at_k(r.retrieved_files, i.expected_files, 5) for i, r in pairs) / n,
            hit_rate_at_5=sum(hit_rate_at_k(r.retrieved_files, i.expected_files, 5) for i, r in pairs) / n,
        )

    overall = compute(all_pairs, "general")
    by_intent = [compute(pairs, intent) for intent, pairs in sorted(groups.items())]

    return MetricsReport(
        tag=tag,
        n_questions=len(all_pairs),
        overall=overall,
        by_intent=by_intent,
        latency_p50_ms=_percentile(latencies, 0.50),
        latency_p95_ms=_percentile(latencies, 0.95),
    )
