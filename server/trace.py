"""Per-call record of how a search reached its results, for the Playground.

Opt-in (`trace=true` on the search endpoints): it costs two extra Qdrant
queries per collection, because the fused query does not say where each hit
ranked on the dense and the BM25 side, and that is exactly what explains a
surprising result.

One instance per call, passed explicitly: `SearchService` is shared by every
request thread, so nothing call-specific may live on it.
"""
from __future__ import annotations

import time
from collections.abc import Generator
from contextlib import contextmanager
from typing import Any


class RetrievalTrace:
    def __init__(self) -> None:
        self._t0 = time.perf_counter()
        self.stages: list[dict[str, Any]] = []
        self._candidates: dict[str, dict[str, Any]] = {}
        # Results are matched to their candidate by object identity: the same
        # objects flow from retrieval through grading, reranking and MMR.
        self._key_of: dict[int, str] = {}
        self._legs: dict[str, tuple[int | None, int | None]] = {}

    @contextmanager
    def stage(self, name: str, **detail: Any) -> Generator[dict[str, Any], None, None]:
        """Time a step; the caller may add details to the yielded dict."""
        record: dict[str, Any] = {"name": name, **detail}
        start = time.perf_counter()
        try:
            yield record
        finally:
            record["ms"] = round((time.perf_counter() - start) * 1000.0, 2)
            self.stages.append(record)

    def note(self, name: str, **detail: Any) -> None:
        """A step with nothing to time: a cache miss, a stage that is off."""
        self.stages.append({"name": name, "ms": None, **detail})

    def legs(self, collection: str, dense_ids: list, sparse_ids: list) -> None:
        """Ranks of each point on the dense and BM25 sides (1-based)."""
        dense = {str(pid): i for i, pid in enumerate(dense_ids, 1)}
        sparse = {str(pid): i for i, pid in enumerate(sparse_ids, 1)}
        for pid in dense.keys() | sparse.keys():
            self._legs[f"{collection}:{pid}"] = (dense.get(pid), sparse.get(pid))

    def pool(self, collection: str, hits: list, results: list, via: str | None = None) -> None:
        """Register retrieved candidates with their fused rank and score."""
        for rank, (hit, result) in enumerate(zip(hits, results), 1):
            key = f"{collection}:{hit.id}"
            dense_rank, sparse_rank = self._legs.get(key, (None, None))
            self._key_of[id(result)] = key
            self._candidates.setdefault(key, {
                "collection": collection,
                "file_path": getattr(result, "file_path", None) or getattr(result, "skill_name", None)
                or f"memory://{getattr(result, 'id', '')}",
                "symbol": getattr(result, "symbol_name", None),
                "dense_rank": dense_rank,
                "sparse_rank": sparse_rank,
                "fused_rank": rank,
                "fused_score": float(hit.score),
                "via": via,
            })

    def annotate(self, result: Any, **fields: Any) -> None:
        key = self._key_of.get(id(result))
        if key is not None:
            self._candidates[key].update(fields)

    def finish(self, final: list) -> dict[str, Any]:
        for rank, result in enumerate(final, 1):
            self.annotate(result, final_rank=rank, final_score=float(result.score))
        candidates = sorted(
            self._candidates.values(),
            key=lambda c: (c.get("final_rank") is None, c.get("final_rank") or 0, -c["fused_score"]),
        )
        return {
            "total_ms": round((time.perf_counter() - self._t0) * 1000.0, 2),
            "stages": self.stages,
            "candidates": candidates,
        }
