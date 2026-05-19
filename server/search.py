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

import time

from core.cache import SemanticCache
from core.collections import COLLECTIONS, DENSE_VECTOR_NAME, SPARSE_VECTOR_NAME
from core.embeddings import EmbeddingService
from core.grader import DocumentGrader
from core.models import (
    CodeSearchResult,
    MemoryResult,
    SearchResult,
    SkillResult,
)
from core.observability import QueryLogger
from core.reranker import CrossEncoderReranker, mmr_select
from core.rewriter import QueryRewriter
from core.router import QueryRouter
from core.sparse import bm25_sparse


# How many candidates each leg of the fusion pulls before RRF merges them.
# 20 each is the canonical Anthropic / Qdrant default; tune via env if needed.
_PREFETCH_LIMIT = 20
# How many candidates each per-collection hybrid query returns before reranking.
_HYBRID_TOP = 20

# Whitelist of payload fields surfaced to MCP/CLI clients in `SearchResult.metadata`.
# Everything else (last_indexed_at, file_mtime, chunk_index, internal flags…) is
# bookkeeping that adds tokens to LLM responses for zero retrieval value.
_METADATA_KEEP: tuple[str, ...] = (
    "symbol_name",
    "package",
    "language",
    "tags",
    "section",
    "doc_title",
    "preamble",
)


def _filter_metadata(payload: dict) -> dict:
    return {k: payload[k] for k in _METADATA_KEEP if k in payload}


class SearchService:
    def __init__(
        self,
        qdrant_client: QdrantClient,
        embedding_service: EmbeddingService,
        reranker: CrossEncoderReranker | None = None,
        mmr_enabled: bool = False,
        mmr_lambda: float = 0.5,
        grader: DocumentGrader | None = None,
        rewriter: QueryRewriter | None = None,
        router: QueryRouter | None = None,
        cache: SemanticCache | None = None,
        query_logger: QueryLogger | None = None,
    ) -> None:
        self._qdrant = qdrant_client
        self._embeddings = embedding_service
        self._reranker = reranker
        self._mmr_enabled = mmr_enabled
        self._mmr_lambda = mmr_lambda
        self._grader = grader
        self._rewriter = rewriter
        self._router = router
        self._cache = cache
        self._query_logger = query_logger

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
                from core.reranker import ScoredDoc

                scored = [ScoredDoc(doc_id=i, score=r.score, payload=r) for i, r in enumerate(ordered)]
                picked = mmr_select(scored, vectors, top_k=limit, lambda_=self._mmr_lambda)
                return [s.payload for s in picked]
            except Exception:
                pass

        return ordered[:limit]

    def _retrieve_all(
        self,
        query: str,
        target_collections: list[str],
        query_filter: Filter | None,
        per_collection: int,
    ) -> list[SearchResult]:
        dense_vec = self._embeddings.embed(query)
        sparse_vec = bm25_sparse(query)
        out: list[SearchResult] = []
        for coll_name in target_collections:
            hits = self._hybrid_query(coll_name, dense_vec, sparse_vec, query_filter, per_collection)
            for hit in hits:
                out.append(
                    SearchResult(
                        content=hit.payload.get("content", ""),
                        file_path=hit.payload.get("file_path", ""),
                        score=hit.score,
                        chunk_type=hit.payload.get("chunk_type", ""),
                        collection=coll_name,
                        metadata=_filter_metadata(hit.payload),
                    )
                )
        return out

    @staticmethod
    def _dedupe_by_path(results: list[SearchResult]) -> list[SearchResult]:
        seen: set[str] = set()
        out: list[SearchResult] = []
        for r in results:
            key = f"{r.collection}::{r.file_path}"
            if key in seen:
                continue
            seen.add(key)
            out.append(r)
        return out

    def search(
        self,
        query: str,
        collections: list[str] | None = None,
        file_types: list[str] | None = None,
        path_filter: str | None = None,
        limit: int = 5,
        tags_any: list[str] | None = None,
        tags_all: list[str] | None = None,
    ) -> list[SearchResult]:
        t_start = time.perf_counter()

        # --- 0. Cache lookup (Phase 4E) ---
        # Cache key includes the limit and filter set so that a more permissive
        # entry can never be reused for a stricter call. Tag scopes also go into
        # the namespace so a result cached for one tag set is never reused for
        # a different one.
        cache_namespace = (
            "search:"
            f"limit={limit}:"
            f"colls={','.join(sorted(collections)) if collections else 'auto'}:"
            f"types={','.join(sorted(file_types)) if file_types else ''}:"
            f"path={path_filter or ''}:"
            f"tagsAny={','.join(sorted(tags_any)) if tags_any else ''}:"
            f"tagsAll={','.join(sorted(tags_all)) if tags_all else ''}"
        )
        if self._cache and self._cache.enabled:
            cached = self._cache.lookup(query, namespace=cache_namespace)
            if cached is not None:
                hit_results = [SearchResult(**r) for r in cached.payload]
                if self._query_logger and self._query_logger.enabled:
                    self._query_logger.log(
                        query,
                        hit_results,
                        latency_ms=(time.perf_counter() - t_start) * 1000.0,
                        intent="search",
                        extra={"cache_hit": True, "cache_score": cached.score},
                    )
                return hit_results

        default_pool = [c.name for c in COLLECTIONS if c.name != "mnemos_memory"]
        target_collections = collections or default_pool

        # Semantic router (Phase 4D): trim the collection set to the most relevant
        # ones. Only kicks in when the caller did NOT pin specific collections,
        # so explicit calls to e.g. `mnemos search --collection x` are honoured.
        if collections is None and self._router and self._router.enabled:
            routed = self._router.route(query, allowed=target_collections)
            if routed:
                target_collections = [r.name for r in routed]

        must_conditions: list = []
        if file_types:
            must_conditions.append(FieldCondition(key="language", match=MatchAny(any=file_types)))
        if path_filter:
            must_conditions.append(FieldCondition(key="file_path", match=MatchValue(value=path_filter)))
        if tags_any:
            # OR semantics across the provided tags — one MatchAny on `tags`.
            must_conditions.append(FieldCondition(key="tags", match=MatchAny(any=list(tags_any))))
        if tags_all:
            # AND semantics — one MatchAny([tag]) condition per tag in the `must`
            # list (Qdrant's `must` is itself an AND).
            for tag in tags_all:
                must_conditions.append(FieldCondition(key="tags", match=MatchAny(any=[tag])))
        query_filter = Filter(must=must_conditions) if must_conditions else None

        # Pull a wider candidate pool per collection when a reranker is going to
        # re-score them; otherwise pulling only `limit` per collection is fine.
        per_collection = _HYBRID_TOP if (self._reranker and self._reranker.enabled) else limit

        # --- 1. Initial retrieval ---
        all_results = self._retrieve_all(query, target_collections, query_filter, per_collection)

        # --- 2. CRAG corrective loop ---
        if self._grader and self._grader.enabled and all_results:
            graded = self._grader.grade(query, [(r.content, r) for r in all_results])
            if self._grader.all_low(graded):
                # Retrieval failed → try rewritten queries
                if self._rewriter and self._rewriter.enabled:
                    for alt in self._rewriter.rewrite(query):
                        if alt == query:
                            continue
                        all_results.extend(
                            self._retrieve_all(alt, target_collections, query_filter, per_collection)
                        )
                    all_results = self._dedupe_by_path(all_results)
            else:
                # Drop the low-graded chunks before reranking
                kept = [(g, r) for g, r in zip(graded, all_results) if g.grade != "low"]
                all_results = [r for _, r in kept]

        # --- 3. Rerank + (optional) MMR ---
        final = self._rerank_and_select(query, all_results, limit)

        # --- 4. Cache write-through ---
        if self._cache and self._cache.enabled and final:
            self._cache.store(query, final, namespace=cache_namespace)

        # --- 5. Observability ---
        if self._query_logger and self._query_logger.enabled:
            self._query_logger.log(
                query,
                final,
                latency_ms=(time.perf_counter() - t_start) * 1000.0,
                intent="search",
                extra={
                    "cache_hit": False,
                    "collections": target_collections,
                    "n_candidates": len(all_results),
                    "reranker": bool(self._reranker and self._reranker.enabled),
                    "grader": bool(self._grader and self._grader.enabled),
                    "router": bool(self._router and self._router.enabled),
                },
            )

        return final

    def search_code(
        self,
        query: str,
        language: str | None = None,
        symbol_type: str | None = None,
        tags_any: list[str] | None = None,
        tags_all: list[str] | None = None,
        path_filter: str | None = None,
        limit: int = 5,
    ) -> list[CodeSearchResult]:
        """Search the single `mnemos_code` collection; scope to a project or
        any other slice via the `tags` payload filter rather than per-project
        collections."""
        dense_vec = self._embeddings.embed(query)
        sparse_vec = bm25_sparse(query)

        must_conditions: list = []
        if tags_any:
            must_conditions.append(FieldCondition(key="tags", match=MatchAny(any=list(tags_any))))
        if tags_all:
            for tag in tags_all:
                must_conditions.append(FieldCondition(key="tags", match=MatchAny(any=[tag])))
        if language:
            must_conditions.append(FieldCondition(key="language", match=MatchValue(value=language)))
        if symbol_type:
            must_conditions.append(FieldCondition(key="chunk_type", match=MatchValue(value=symbol_type)))
        if path_filter:
            must_conditions.append(FieldCondition(key="file_path", match=MatchValue(value=path_filter)))
        query_filter = Filter(must=must_conditions) if must_conditions else None

        per_collection = _HYBRID_TOP if (self._reranker and self._reranker.enabled) else limit
        hits = self._hybrid_query("mnemos_code", dense_vec, sparse_vec, query_filter, per_collection)

        all_results: list[CodeSearchResult] = [
            CodeSearchResult(
                content=hit.payload.get("content", ""),
                file_path=hit.payload.get("file_path", ""),
                score=hit.score,
                chunk_type=hit.payload.get("chunk_type", ""),
                collection="mnemos_code",
                metadata={"tags": hit.payload.get("tags", [])},
                language=hit.payload.get("language", ""),
                symbol_name=hit.payload.get("symbol_name"),
                package=hit.payload.get("package"),
            )
            for hit in hits
        ]

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
        tags_any: list[str] | None = None,
        tags_all: list[str] | None = None,
        memory_type: str | None = None,
        limit: int = 5,
    ) -> list[MemoryResult]:
        dense_vec = self._embeddings.embed(query)
        sparse_vec = bm25_sparse(query)

        must_conditions = [FieldCondition(key="status", match=MatchValue(value="approved"))]
        if tags_any:
            must_conditions.append(FieldCondition(key="tags", match=MatchAny(any=list(tags_any))))
        if tags_all:
            for tag in tags_all:
                must_conditions.append(FieldCondition(key="tags", match=MatchAny(any=[tag])))
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
