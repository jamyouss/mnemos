"""Cross-encoder reranker — re-orders retrieved candidates by joint scoring
of (query, document) pairs. The single biggest precision gain in modern RAG.

Default model: BAAI/bge-reranker-v2-m3 (multilingual, ~568MB).
Alternative: mixedbread-ai/mxbai-rerank-large-v2 (Qwen-based, faster).

Heavy dependencies (`rerankers`, `torch`, `transformers`) are imported lazily
so the module is cheap to import when reranking is disabled.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger("mnemos.reranker")


@dataclass
class ScoredDoc:
    """A retrieved document + its rerank score."""

    doc_id: int                  # original index in the candidate list
    score: float
    payload: Any                 # opaque — the caller decides what to pass in


class CrossEncoderReranker:
    """Re-rank candidates with a cross-encoder model. Lazy model load."""

    def __init__(
        self,
        model_name: str = "BAAI/bge-reranker-v2-m3",
        model_type: str = "cross-encoder",
        enabled: bool = True,
        device: str | None = None,
    ) -> None:
        self._model_name = model_name
        self._model_type = model_type
        self._enabled = enabled
        self._device = device
        self._model = None  # lazy-loaded on first use

    @property
    def enabled(self) -> bool:
        return self._enabled

    def _load(self):
        if self._model is not None:
            return self._model
        try:
            from rerankers import Reranker
        except ImportError as exc:
            raise RuntimeError(
                "rerankers lib is required. Install with: "
                "pip install 'rerankers[transformers]'"
            ) from exc

        logger.info("Loading reranker model: %s (%s)", self._model_name, self._model_type)
        kwargs: dict = {"model_type": self._model_type}
        if self._device:
            kwargs["device"] = self._device
        self._model = Reranker(self._model_name, **kwargs)
        return self._model

    def rerank(
        self,
        query: str,
        candidates: list[tuple[str, Any]],
        top_k: int | None = None,
    ) -> list[ScoredDoc]:
        """Rerank `candidates` (list of (text, payload)) by relevance to `query`.

        Returns top_k ScoredDocs in descending score order.
        If reranking is disabled, returns the candidates in original order with
        synthetic descending scores so callers can treat output uniformly.
        """
        if not candidates:
            return []

        if not self._enabled:
            n = len(candidates)
            return [
                ScoredDoc(doc_id=i, score=float(n - i), payload=payload)
                for i, (_, payload) in enumerate(candidates[: top_k or n])
            ]

        model = self._load()
        texts = [text for text, _ in candidates]
        try:
            ranked = model.rank(query=query, docs=texts)
        except Exception:
            logger.exception("Reranker failed — falling back to original order")
            n = len(candidates)
            return [
                ScoredDoc(doc_id=i, score=float(n - i), payload=payload)
                for i, (_, payload) in enumerate(candidates[: top_k or n])
            ]

        # rerankers normalises to a list of Result objects with .doc_id and .score
        ordered: list[ScoredDoc] = []
        for r in ranked.results:
            idx = int(getattr(r, "doc_id", 0))
            score = float(getattr(r, "score", 0.0))
            payload = candidates[idx][1]
            ordered.append(ScoredDoc(doc_id=idx, score=score, payload=payload))

        if top_k is not None:
            ordered = ordered[:top_k]
        return ordered


def mmr_select(
    scored: list[ScoredDoc],
    embeddings: list[list[float]],
    top_k: int,
    lambda_: float = 0.5,
) -> list[ScoredDoc]:
    """Maximum Marginal Relevance — pick top_k items balancing relevance vs novelty.

    Inputs:
      scored      : reranker output (descending by relevance)
      embeddings  : per-candidate dense embedding (same order as scored)
      top_k       : number of items to return
      lambda_     : 0 = pure novelty, 1 = pure relevance (0.5 = balanced)
    """
    if not scored or top_k <= 0:
        return []
    if top_k >= len(scored):
        return scored

    import numpy as np

    if not embeddings or len(embeddings) != len(scored):
        # No embeddings supplied — fall back to plain top-K by score.
        return scored[:top_k]

    matrix = np.array(embeddings, dtype=np.float32)
    # Cosine similarity assumes inputs are already L2-normalised (sentence-transformers does this).
    sim = matrix @ matrix.T

    relevance = np.array([s.score for s in scored], dtype=np.float32)
    # Normalise relevance to [0, 1] so it mixes cleanly with cosine similarity.
    if relevance.max() > relevance.min():
        relevance = (relevance - relevance.min()) / (relevance.max() - relevance.min())

    selected: list[int] = []
    remaining = list(range(len(scored)))

    # Start with the most relevant document
    first = int(np.argmax(relevance))
    selected.append(first)
    remaining.remove(first)

    while remaining and len(selected) < top_k:
        best_idx = -1
        best_score = -float("inf")
        for cand in remaining:
            max_sim_to_selected = max(sim[cand][s] for s in selected)
            mmr_score = lambda_ * relevance[cand] - (1 - lambda_) * max_sim_to_selected
            if mmr_score > best_score:
                best_score = mmr_score
                best_idx = cand
        if best_idx < 0:
            break
        selected.append(best_idx)
        remaining.remove(best_idx)

    return [scored[i] for i in selected]
