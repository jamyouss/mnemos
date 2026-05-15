from __future__ import annotations

from qdrant_client import QdrantClient
from qdrant_client.models import (
    FieldCondition,
    Filter,
    Fusion,
    FusionQuery,
    MatchAny,
    MatchValue,
    Prefetch,
    SparseVector,
)

from rag_core.collections import COLLECTIONS, DENSE_VECTOR_NAME, SPARSE_VECTOR_NAME
from rag_core.embeddings import EmbeddingService
from rag_core.models import (
    CodeSearchResult,
    MemoryResult,
    SearchResult,
    SkillResult,
)
from rag_core.reranker import CrossEncoderReranker, mmr_select
from rag_core.sparse import bm25_sparse


# How many candidates each leg of the fusion pulls before RRF merges them.
# 20 each is the canonical Anthropic / Qdrant default; tune via env if needed.
_PREFETCH_LIMIT = 20
# How many candidates each per-collection hybrid query returns before reranking.
_HYBRID_TOP = 20


class SearchService:
    def __init__(
        self,
        qdrant_client: QdrantClient,
        embedding_service: EmbeddingService,
        reranker: CrossEncoderReranker | None = None,
        mmr_enabled: bool = False,
        mmr_lambda: float = 0.5,
    ) -> None:
        self._qdrant = qdrant_client
        self._embeddings = embedding_service
        self._reranker = reranker
        self._mmr_enabled = mmr_enabled
        self._mmr_lambda = mmr_lambda

    # ------------------------------------------------------------------
    # Core hybrid retrieval — used by every public search method.
    # ------------------------------------------------------------------

    def _hybrid_query(
        self,
        collection: str,
        dense_vec: list[float],
        sparse_vec: SparseVector,
        query_filter: Filter | None,
        limit: int,
    ):
        """Run a single-collection hybrid query (dense + sparse) fused by RRF."""
        try:
            return self._qdrant.query_points(
                collection_name=collection,
                prefetch=[
                    Prefetch(
                        query=dense_vec,
                        using=DENSE_VECTOR_NAME,
                        limit=_PREFETCH_LIMIT,
                        filter=query_filter,
                    ),
                    Prefetch(
                        query=sparse_vec,
                        using=SPARSE_VECTOR_NAME,
                        limit=_PREFETCH_LIMIT,
                        filter=query_filter,
                    ),
                ],
                query=FusionQuery(fusion=Fusion.RRF),
                limit=limit,
                with_payload=True,
            ).points
        except Exception:
            # Legacy collections still on the unnamed-dense schema: fall back to dense-only.
            return self._qdrant.query_points(
                collection_name=collection,
                query=dense_vec,
                query_filter=query_filter,
                limit=limit,
                with_payload=True,
            ).points

    def _rerank_and_select(
        self,
        query: str,
        results: list,
        limit: int,
    ) -> list:
        """Apply the cross-encoder reranker (if enabled) and optional MMR top-K selection."""
        if not results:
            return results

        if self._reranker is None or not self._reranker.enabled:
            results.sort(key=lambda r: r.score, reverse=True)
            return results[:limit]

        candidates = [(r.content, r) for r in results]
        ranked = self._reranker.rerank(query, candidates, top_k=None)

        ordered = [s.payload for s in ranked]
        # Map reranker score back onto the SearchResult so the API surfaces it.
        for s in ranked:
            s.payload.score = float(s.score)

        if self._mmr_enabled and len(ordered) > limit:
            try:
                texts = [r.content for r in ordered]
                vectors = self._embeddings.embed_batch(texts)
                from rag_core.reranker import ScoredDoc

                scored = [ScoredDoc(doc_id=i, score=r.score, payload=r) for i, r in enumerate(ordered)]
                picked = mmr_select(scored, vectors, top_k=limit, lambda_=self._mmr_lambda)
                return [s.payload for s in picked]
            except Exception:
                pass

        return ordered[:limit]

    def search(
        self,
        query: str,
        collections: list[str] | None = None,
        file_types: list[str] | None = None,
        path_filter: str | None = None,
        limit: int = 5,
    ) -> list[SearchResult]:
        dense_vec = self._embeddings.embed(query)
        sparse_vec = bm25_sparse(query)

        target_collections = collections or [
            c.name for c in COLLECTIONS if c.name != "mnemos_memory"
        ]

        must_conditions: list = []
        if file_types:
            must_conditions.append(FieldCondition(key="language", match=MatchAny(any=file_types)))
        if path_filter:
            must_conditions.append(FieldCondition(key="file_path", match=MatchValue(value=path_filter)))
        query_filter = Filter(must=must_conditions) if must_conditions else None

        # Pull a wider candidate pool per collection when a reranker is going to
        # re-score them; otherwise pulling only `limit` per collection is fine.
        per_collection = _HYBRID_TOP if (self._reranker and self._reranker.enabled) else limit

        all_results: list[SearchResult] = []
        for coll_name in target_collections:
            hits = self._hybrid_query(coll_name, dense_vec, sparse_vec, query_filter, per_collection)
            for hit in hits:
                all_results.append(
                    SearchResult(
                        content=hit.payload.get("content", ""),
                        file_path=hit.payload.get("file_path", ""),
                        score=hit.score,
                        chunk_type=hit.payload.get("chunk_type", ""),
                        collection=coll_name,
                        metadata={
                            k: v
                            for k, v in hit.payload.items()
                            if k not in ("content", "file_path", "chunk_type")
                        },
                    )
                )

        return self._rerank_and_select(query, all_results, limit)

    def search_code(
        self,
        query: str,
        language: str | None = None,
        symbol_type: str | None = None,
        project: str | None = None,
        path_filter: str | None = None,
        limit: int = 5,
    ) -> list[CodeSearchResult]:
        dense_vec = self._embeddings.embed(query)
        sparse_vec = bm25_sparse(query)

        if project:
            collections = [f"mnemos_code_{project}"]
        else:
            collections = [c.name for c in COLLECTIONS if c.name.startswith("mnemos_code_")]

        must_conditions: list = []
        if language:
            must_conditions.append(FieldCondition(key="language", match=MatchValue(value=language)))
        if symbol_type:
            must_conditions.append(FieldCondition(key="chunk_type", match=MatchValue(value=symbol_type)))
        if path_filter:
            must_conditions.append(FieldCondition(key="file_path", match=MatchValue(value=path_filter)))
        query_filter = Filter(must=must_conditions) if must_conditions else None

        per_collection = _HYBRID_TOP if (self._reranker and self._reranker.enabled) else limit

        all_results: list[CodeSearchResult] = []
        for coll_name in collections:
            hits = self._hybrid_query(coll_name, dense_vec, sparse_vec, query_filter, per_collection)
            for hit in hits:
                all_results.append(
                    CodeSearchResult(
                        content=hit.payload.get("content", ""),
                        file_path=hit.payload.get("file_path", ""),
                        score=hit.score,
                        chunk_type=hit.payload.get("chunk_type", ""),
                        collection=coll_name,
                        metadata={},
                        language=hit.payload.get("language", ""),
                        symbol_name=hit.payload.get("symbol_name"),
                        package=hit.payload.get("package"),
                    )
                )

        return self._rerank_and_select(query, all_results, limit)

    def search_skills(self, query: str, limit: int = 3) -> list[SkillResult]:
        dense_vec = self._embeddings.embed(query)
        sparse_vec = bm25_sparse(query)
        per_collection = _HYBRID_TOP if (self._reranker and self._reranker.enabled) else limit
        hits = self._hybrid_query("mnemos_skills", dense_vec, sparse_vec, None, per_collection)
        results = [
            SkillResult(
                skill_name=hit.payload.get("skill_name", ""),
                description=hit.payload.get("description", ""),
                score=hit.score,
                instructions_preview=hit.payload.get("content", "")[:200],
            )
            for hit in hits
        ]
        # Reranker uses .content; SkillResult has instructions_preview instead — wrap it.
        if self._reranker is not None and self._reranker.enabled and results:
            candidates = [(r.instructions_preview, r) for r in results]
            ranked = self._reranker.rerank(query, candidates, top_k=limit)
            return [s.payload for s in ranked]
        return results[:limit]

    def search_memory(
        self,
        query: str,
        project: str | None = None,
        memory_type: str | None = None,
        limit: int = 5,
    ) -> list[MemoryResult]:
        dense_vec = self._embeddings.embed(query)
        sparse_vec = bm25_sparse(query)

        must_conditions = [FieldCondition(key="status", match=MatchValue(value="approved"))]
        if project:
            must_conditions.append(FieldCondition(key="project", match=MatchValue(value=project)))
        if memory_type:
            must_conditions.append(FieldCondition(key="memory_type", match=MatchValue(value=memory_type)))
        query_filter = Filter(must=must_conditions)

        per_collection = _HYBRID_TOP if (self._reranker and self._reranker.enabled) else limit
        hits = self._hybrid_query("mnemos_memory", dense_vec, sparse_vec, query_filter, per_collection)
        results = [
            MemoryResult(
                id=hit.payload.get("id", ""),
                content=hit.payload.get("content", ""),
                project=hit.payload.get("project"),
                topic=hit.payload.get("topic"),
                memory_type=hit.payload.get("memory_type", ""),
                tags=hit.payload.get("tags", []),
                score=hit.score,
                created_at=hit.payload.get("created_at", ""),
            )
            for hit in hits
        ]
        if self._reranker is not None and self._reranker.enabled and results:
            candidates = [(r.content, r) for r in results]
            ranked = self._reranker.rerank(query, candidates, top_k=limit)
            return [s.payload for s in ranked]
        return results[:limit]
