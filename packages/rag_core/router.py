"""Semantic query router — pick the most relevant collections for a query.

For each collection we pre-compute a dense embedding of its `description`
field at startup. At query time we cosine-compare the query embedding against
those collection embeddings and return the top-K most relevant collections.

Falls back to ALL collections when:
- routing is disabled
- the top score is below `min_score` (ambiguous query)
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

from rag_core.collections import COLLECTIONS, CollectionConfig
from rag_core.embeddings import EmbeddingService

logger = logging.getLogger("mnemos.router")

_MIN_SCORE_FALLBACK = 0.4
_DEFAULT_TOP_K = 2


@dataclass
class RoutedCollection:
    name: str
    score: float


class QueryRouter:
    """Cosine-similarity router over collection descriptions."""

    def __init__(
        self,
        embedding_service: EmbeddingService,
        enabled: bool = True,
        top_k: int = _DEFAULT_TOP_K,
        min_score: float = _MIN_SCORE_FALLBACK,
        collections: list[CollectionConfig] | None = None,
    ) -> None:
        self._embeddings = embedding_service
        self._enabled = enabled
        self._top_k = max(1, int(top_k))
        self._min_score = float(min_score)

        configs = collections if collections is not None else COLLECTIONS
        # Skip collections with no description — they can't be routed against.
        self._configs = [c for c in configs if c.description and c.description.strip()]
        if self._enabled and self._configs:
            descriptions = [c.description for c in self._configs]
            self._vectors = self._embeddings.embed_batch(descriptions)
        else:
            self._vectors = []

    @property
    def enabled(self) -> bool:
        return self._enabled

    def route(
        self,
        query: str,
        allowed: list[str] | None = None,
    ) -> list[RoutedCollection]:
        """Return the top-K most relevant collections.

        If `allowed` is provided, route is restricted to that subset (useful for
        forced single-collection searches like `/api/search-skills`).
        On low max-score or when disabled, returns ALL allowed collections so
        callers degrade to the legacy "fan out everywhere" behaviour.
        """
        all_names = [c.name for c in self._configs]
        if allowed is not None:
            allow_set = set(allowed)
            all_names = [n for n in all_names if n in allow_set]

        if not self._enabled or not all_names:
            return [RoutedCollection(name=n, score=1.0) for n in all_names]

        q_vec = self._embeddings.embed(query)
        scored: list[RoutedCollection] = []
        for config, vec in zip(self._configs, self._vectors):
            if config.name not in all_names:
                continue
            score = _cosine(q_vec, vec)
            scored.append(RoutedCollection(name=config.name, score=score))

        if not scored:
            return []

        scored.sort(key=lambda r: r.score, reverse=True)
        if scored[0].score < self._min_score:
            # Ambiguous → fall back to all allowed collections
            return [RoutedCollection(name=n, score=scored[0].score) for n in all_names]

        return scored[: self._top_k]


def _cosine(a: list[float], b: list[float]) -> float:
    # Embeddings are L2-normalised by sentence-transformers, so a dot product
    # is already the cosine similarity. Robust against floating-point drift.
    s = 0.0
    for x, y in zip(a, b):
        s += x * y
    return s
