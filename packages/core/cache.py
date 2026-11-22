"""Semantic cache — returns previously-served results when a new query is
sufficiently close (cosine sim ≥ threshold) to one already in the cache.

Stores entries in a dedicated Qdrant collection (`mnemos_cache`) with the dense
embedding of the original query as the vector and the serialised results as
payload. Each entry has a TTL; expired entries are filtered at lookup time.
"""
from __future__ import annotations

import json
import logging
import time
import uuid
from dataclasses import dataclass
from typing import Any

from qdrant_client import QdrantClient
from qdrant_client.models import (
    Distance,
    PointStruct,
    VectorParams,
)

from core.embeddings import EmbeddingService

logger = logging.getLogger("mnemos.cache")

CACHE_COLLECTION = "mnemos_cache"
_DEFAULT_THRESHOLD = 0.95
_DEFAULT_TTL_SECONDS = 3600


@dataclass
class CacheHit:
    """A successful cache lookup."""

    query: str
    age_seconds: float
    score: float          # cosine similarity to the cached query
    payload: list[dict]   # the cached results


class SemanticCache:
    """Cosine-similarity cache for retrieval results."""

    def __init__(
        self,
        qdrant_client: QdrantClient,
        embedding_service: EmbeddingService,
        enabled: bool = True,
        threshold: float = _DEFAULT_THRESHOLD,
        ttl_seconds: int = _DEFAULT_TTL_SECONDS,
    ) -> None:
        self._qdrant = qdrant_client
        self._embeddings = embedding_service
        self._enabled = enabled
        self._threshold = float(threshold)
        self._ttl = int(ttl_seconds)
        if self._enabled:
            self._ensure_collection()

    @property
    def enabled(self) -> bool:
        return self._enabled

    @enabled.setter
    def enabled(self, value: bool) -> None:
        # A cache built disabled never created its collection.
        if value and not self._enabled:
            self._ensure_collection()
        self._enabled = bool(value)

    @property
    def threshold(self) -> float:
        return self._threshold

    @threshold.setter
    def threshold(self, value: float) -> None:
        self._threshold = float(value)

    @property
    def ttl_seconds(self) -> int:
        return self._ttl

    @ttl_seconds.setter
    def ttl_seconds(self, value: int) -> None:
        self._ttl = int(value)

    def _ensure_collection(self) -> None:
        dim = self._embeddings.dimension
        if self._qdrant.collection_exists(CACHE_COLLECTION):
            vectors = self._qdrant.get_collection(CACHE_COLLECTION).config.params.vectors
            if vectors.size == dim:
                return
            # Entries from another embedding model can never match again.
            self._qdrant.delete_collection(CACHE_COLLECTION)
        # The cache uses an unnamed dense vector (simple lookup, no hybrid needed).
        self._qdrant.create_collection(
            collection_name=CACHE_COLLECTION,
            vectors_config=VectorParams(size=dim, distance=Distance.COSINE),
        )

    def _bucket(self, namespace: str | None) -> str:
        """Namespacing key — separate code/skills/general lookups in the same collection."""
        return namespace or "default"

    def lookup(self, query: str, namespace: str | None = None) -> CacheHit | None:
        """Return a CacheHit if a sufficiently-close query exists; else None."""
        if not self._enabled or not query.strip():
            return None
        try:
            vec = self._embeddings.embed_query(query)
            hits = self._qdrant.query_points(
                collection_name=CACHE_COLLECTION,
                query=vec,
                limit=1,
                with_payload=True,
            ).points
        except Exception:
            # Cache must never break the request — fail open.
            return None

        if not hits:
            return None
        top = hits[0]
        if top.score < self._threshold:
            return None
        if top.payload.get("namespace") != self._bucket(namespace):
            return None

        created_at = float(top.payload.get("created_at", 0.0))
        age = time.time() - created_at
        if self._ttl > 0 and age > self._ttl:
            # Expired — best-effort delete and miss.
            try:
                self._qdrant.delete(collection_name=CACHE_COLLECTION, points_selector=[top.id])
            except Exception:
                pass
            return None

        try:
            payload = json.loads(top.payload.get("results", "[]"))
        except (json.JSONDecodeError, TypeError):
            return None
        return CacheHit(
            query=top.payload.get("query", query),
            age_seconds=age,
            score=float(top.score),
            payload=payload,
        )

    def store(self, query: str, results: list[Any], namespace: str | None = None) -> None:
        """Insert (or upsert) a cache entry for `query`."""
        if not self._enabled or not query.strip():
            return
        try:
            vec = self._embeddings.embed_query(query)
            serialised = json.dumps(_to_jsonable(results), ensure_ascii=False)
        except Exception:
            return

        point_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"{self._bucket(namespace)}::{query}"))
        payload = {
            "query": query,
            "namespace": self._bucket(namespace),
            "results": serialised,
            "created_at": time.time(),
        }
        try:
            self._qdrant.upsert(
                collection_name=CACHE_COLLECTION,
                points=[PointStruct(id=point_id, vector=vec, payload=payload)],
            )
        except Exception:
            logger.debug("Cache store failed for query=%r", query[:60])

    def invalidate(self) -> None:
        """Drop all cached entries — call after a reindex."""
        if not self._enabled:
            return
        try:
            self._qdrant.delete_collection(collection_name=CACHE_COLLECTION)
        except Exception:
            pass
        try:
            self._ensure_collection()
        except Exception:
            pass


def _to_jsonable(obj: Any) -> Any:
    """Best-effort coercion of pydantic models / dataclasses to plain dicts."""
    if hasattr(obj, "model_dump"):
        return obj.model_dump()
    if isinstance(obj, list):
        return [_to_jsonable(x) for x in obj]
    if isinstance(obj, dict):
        return {k: _to_jsonable(v) for k, v in obj.items()}
    return obj
