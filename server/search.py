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
    Rrf,
    RrfQuery,
    SparseVector,
)

import time
from typing import Any

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
from core.sparse import bm25_sparse

from server.trace import RetrievalTrace


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
    "source_url",
)


def _filter_metadata(payload: dict) -> dict:
    return {k: payload[k] for k in _METADATA_KEEP if k in payload}


# Preview mode budget. Most code chunks fit comfortably; only long
# Go functions / Markdown sections get truncated. Callers asking for
# `mode="full"` bypass this entirely.
_PREVIEW_MAX_LINES = 30
_PREVIEW_MAX_CHARS = 1500
_TRUNCATION_MARKER = "\n… [truncated — call again with mode=\"full\" or a narrower path_filter]"


def _truncate_preview(text: str) -> tuple[str, bool]:
    """Return (text, truncated) where the text is capped to the preview budget.

    Truncation triggers on whichever budget (lines or chars) hits first.
    """
    if not text:
        return text, False
    char_budget_hit = len(text) > _PREVIEW_MAX_CHARS
    lines = text.splitlines()
    line_budget_hit = len(lines) > _PREVIEW_MAX_LINES
    if not (char_budget_hit or line_budget_hit):
        return text, False
    # Take min of both budgets so we never overshoot either.
    cut_by_lines = "\n".join(lines[:_PREVIEW_MAX_LINES])
    cut_by_chars = text[:_PREVIEW_MAX_CHARS]
    truncated = cut_by_lines if len(cut_by_lines) <= len(cut_by_chars) else cut_by_chars
    return truncated.rstrip() + _TRUNCATION_MARKER, True


def _apply_preview_mode(results: list, mode: str) -> list:
    """Mutate each result's `content` to a preview when `mode == "preview"`.

    Sets `metadata["truncated"] = True` on results that were cut so the LLM
    caller knows it can re-fetch in `mode="full"` to see the rest.
    """
    if mode != "preview" or not results:
        return results
    for r in results:
        new_content, truncated = _truncate_preview(r.content)
        if truncated:
            r.content = new_content
            # SearchResult.metadata is a plain dict on the base model; on
            # subclasses with dedicated fields it's still present.
            meta = getattr(r, "metadata", None)
            if isinstance(meta, dict):
                meta["truncated"] = True
    return results


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
        cache: SemanticCache | None = None,
        query_logger: QueryLogger | None = None,
        dense_weight: float = 3.0,
    ) -> None:
        self._qdrant = qdrant_client
        self._embeddings = embedding_service
        self._reranker = reranker
        self._mmr_enabled = mmr_enabled
        self._mmr_lambda = mmr_lambda
        self._grader = grader
        self._rewriter = rewriter
        self._cache = cache
        self._query_logger = query_logger
        # Equal-weight RRF let BM25 drag the ranking down: camelCase/snake
        # splitting makes a symbol match every file sharing its words. Weighting
        # the dense side 3:1 lifted code-search MRR 0.55 → 0.74 (docs/EVAL.md)
        # while keeping BM25 for literal strings. 1.0 is the old plain RRF.
        self._fusion = (
            FusionQuery(fusion=Fusion.RRF)
            if dense_weight == 1.0
            else RrfQuery(rrf=Rrf(weights=[dense_weight, 1.0]))
        )

    @property
    def mmr_enabled(self) -> bool:
        return self._mmr_enabled

    @mmr_enabled.setter
    def mmr_enabled(self, value: bool) -> None:
        self._mmr_enabled = bool(value)

    @property
    def mmr_lambda(self) -> float:
        return self._mmr_lambda

    @mmr_lambda.setter
    def mmr_lambda(self, value: float) -> None:
        self._mmr_lambda = float(value)

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
        trace: RetrievalTrace | None = None,
    ):
        """Run a single-collection hybrid query (dense + sparse) fused by RRF."""
        try:
            points = self._qdrant.query_points(
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
                query=self._fusion,
                limit=limit,
                with_payload=True,
            ).points
        except Exception:
            # Legacy collections still on the unnamed-dense schema: fall back to dense-only.
            if trace is not None:
                trace.note("fusion", collection=collection, mode="dense-only (legacy schema)")
            return self._qdrant.query_points(
                collection_name=collection,
                query=dense_vec,
                query_filter=query_filter,
                limit=limit,
                with_payload=True,
            ).points
        if trace is not None:
            # The fused query does not say where each hit stood on either side;
            # asking each side separately is the only way to know.
            legs = [
                self._qdrant.query_points(
                    collection_name=collection,
                    query=vec,
                    using=name,
                    query_filter=query_filter,
                    limit=_PREFETCH_LIMIT,
                    with_payload=False,
                ).points
                for vec, name in ((dense_vec, DENSE_VECTOR_NAME), (sparse_vec, SPARSE_VECTOR_NAME))
            ]
            trace.legs(collection, [p.id for p in legs[0]], [p.id for p in legs[1]])
        return points

    def _rerank(self, query: str, candidates: list[tuple[str, Any]], top_k: int | None,
                trace: RetrievalTrace | None):
        """Run the cross-encoder; with a trace, time it and record each verdict."""
        if trace is None:
            return self._reranker.rerank(query, candidates, top_k=top_k)
        with trace.stage("rerank", candidates=len(candidates)):
            ranked = self._reranker.rerank(query, candidates, top_k=top_k)
        for rank, s in enumerate(ranked, 1):
            trace.annotate(s.payload, rerank_score=float(s.score), rerank_rank=rank)
        return ranked

    def _rerank_and_select(
        self,
        query: str,
        results: list,
        limit: int,
        trace: RetrievalTrace | None = None,
    ) -> list:
        """Apply the cross-encoder reranker (if enabled) and optional MMR top-K selection."""
        if not results:
            return results

        if self._reranker is None or not self._reranker.enabled:
            if trace is not None:
                trace.note("rerank", enabled=False)
            results.sort(key=lambda r: r.score, reverse=True)
            return results[:limit]

        ranked = self._rerank(query, [(r.content, r) for r in results], None, trace)

        ordered = [s.payload for s in ranked]
        # Map reranker score back onto the SearchResult so the API surfaces it.
        for s in ranked:
            s.payload.score = float(s.score)

        if self._mmr_enabled and len(ordered) > limit:
            try:
                texts = [r.content for r in ordered]
                vectors = self._embeddings.embed_documents(texts)
                from core.reranker import ScoredDoc

                scored = [ScoredDoc(doc_id=i, score=r.score, payload=r) for i, r in enumerate(ordered)]
                picked = mmr_select(scored, vectors, top_k=limit, lambda_=self._mmr_lambda)
                if trace is not None:
                    trace.note("mmr", enabled=True, picked=len(picked), lambda_=self._mmr_lambda)
                return [s.payload for s in picked]
            except Exception as exc:
                if trace is not None:
                    trace.note("mmr", enabled=True, error=type(exc).__name__)
        elif trace is not None:
            trace.note("mmr", enabled=self._mmr_enabled, applied=False)

        return ordered[:limit]

    def _embed(self, query: str, trace: RetrievalTrace | None, task: str | None = None):
        """Dense and BM25 vectors of the query, timed when tracing."""
        kwargs = {"task": task} if task else {}
        if trace is None:
            return self._embeddings.embed_query(query, **kwargs), bm25_sparse(query)
        with trace.stage("embed", query=query):
            return self._embeddings.embed_query(query, **kwargs), bm25_sparse(query)

    def _query_collection(self, collection: str, dense_vec, sparse_vec, query_filter: Filter | None,
                          limit: int, trace: RetrievalTrace | None):
        """`_hybrid_query`, timed when tracing."""
        if trace is None:
            return self._hybrid_query(collection, dense_vec, sparse_vec, query_filter, limit)
        with trace.stage("retrieve", collection=collection) as record:
            hits = self._hybrid_query(collection, dense_vec, sparse_vec, query_filter, limit, trace)
            record["candidates"] = len(hits)
        return hits

    def _retrieve_all(
        self,
        query: str,
        target_collections: list[str],
        query_filter: Filter | None,
        per_collection: int,
        trace: RetrievalTrace | None = None,
        via: str | None = None,
    ) -> list[SearchResult]:
        dense_vec, sparse_vec = self._embed(query, trace)
        out: list[SearchResult] = []
        for coll_name in target_collections:
            hits = self._query_collection(coll_name, dense_vec, sparse_vec, query_filter, per_collection, trace)
            results = [
                SearchResult(
                    content=hit.payload.get("content", ""),
                    file_path=hit.payload.get("file_path", ""),
                    score=hit.score,
                    chunk_type=hit.payload.get("chunk_type", ""),
                    collection=coll_name,
                    metadata=_filter_metadata(hit.payload),
                )
                for hit in hits
            ]
            if trace is not None:
                trace.pool(coll_name, hits, results, via)
            out.extend(results)
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

    def _log_query(
        self,
        query: str,
        results: list,
        t_start: float,
        intent: str,
        extra: dict | None = None,
        source: str | None = None,
    ) -> None:
        """Record one retrieval call, if the query log is on.

        Every public search entry point goes through here. `search_code` in
        particular is the busiest path — it backs the `mnemos_search_code` MCP
        tool — and leaving it uninstrumented made the log blind to the traffic
        that matters most when judging a retrieval change.
        """
        if not (self._query_logger and self._query_logger.enabled):
            return
        # Only the reranker is shared by every entry point. The cache, grader
        # and rewriter live in `search()` alone, so recording them here
        # for `search_code` would describe the service configuration rather
        # than the path the call actually took — and a log that overstates
        # what ran is worse than no log when judging a retrieval change.
        stages = {"reranker": bool(self._reranker and self._reranker.enabled)}
        self._query_logger.log(
            query,
            results,
            latency_ms=(time.perf_counter() - t_start) * 1000.0,
            intent=intent,
            extra={**stages, **(extra or {}), **({"source": source} if source else {})},
        )

    def search(
        self,
        query: str,
        collections: list[str] | None = None,
        file_types: list[str] | None = None,
        path_filter: str | None = None,
        limit: int = 5,
        tags_any: list[str] | None = None,
        tags_all: list[str] | None = None,
        mode: str = "preview",
        use_cache: bool = True,
        trace: RetrievalTrace | None = None,
        source: str | None = None,
    ) -> list[SearchResult]:
        t_start = time.perf_counter()
        cache_on = bool(self._cache and self._cache.enabled and use_cache)

        # --- 0. Cache lookup (Phase 4E) ---
        # Cache key includes the limit and filter set so that a more permissive
        # entry can never be reused for a stricter call. Tag scopes also go into
        # the namespace so a result cached for one tag set is never reused for
        # a different one.
        cache_namespace = (
            "search:"
            f"limit={limit}:"
            f"mode={mode}:"
            f"colls={','.join(sorted(collections)) if collections else 'auto'}:"
            f"types={','.join(sorted(file_types)) if file_types else ''}:"
            f"path={path_filter or ''}:"
            f"tagsAny={','.join(sorted(tags_any)) if tags_any else ''}:"
            f"tagsAll={','.join(sorted(tags_all)) if tags_all else ''}"
        )
        if cache_on:
            cached = self._cache.lookup(query, namespace=cache_namespace)
            if trace is not None:
                trace.note("cache", hit=cached is not None,
                           score=float(cached.score) if cached is not None else None)
            if cached is not None:
                hit_results = [SearchResult(**r) for r in cached.payload]
                if self._query_logger and self._query_logger.enabled:
                    self._query_logger.log(
                        query,
                        hit_results,
                        latency_ms=(time.perf_counter() - t_start) * 1000.0,
                        intent="search",
                        extra={"cache_hit": True, "cache_score": cached.score,
                               **({"source": source} if source else {})},
                    )
                return hit_results
        elif trace is not None:
            trace.note("cache", hit=False, skipped="bypassed" if use_cache is False else "disabled")

        default_pool = [c.name for c in COLLECTIONS if c.name != "mnemos_memory"]
        target_collections = collections or default_pool

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
        all_results = self._retrieve_all(query, target_collections, query_filter, per_collection, trace)

        # --- 2. CRAG corrective loop ---
        if self._grader and self._grader.enabled and all_results:
            if trace is None:
                graded = self._grader.grade(query, [(r.content, r) for r in all_results])
            else:
                with trace.stage("grader", candidates=len(all_results)) as record:
                    graded = self._grader.grade(query, [(r.content, r) for r in all_results])
                    record["grades"] = {g: sum(1 for x in graded if x.grade == g) for g in {x.grade for x in graded}}
                for g, r in zip(graded, all_results):
                    trace.annotate(r, grade=g.grade)
            if self._grader.all_low(graded):
                # Retrieval failed → try rewritten queries
                if self._rewriter and self._rewriter.enabled:
                    variants = [alt for alt in self._rewriter.rewrite(query) if alt != query]
                    if trace is not None:
                        trace.note("rewriter", variants=variants)
                    for alt in variants:
                        all_results.extend(
                            self._retrieve_all(alt, target_collections, query_filter, per_collection, trace, via=alt)
                        )
                    all_results = self._dedupe_by_path(all_results)
            else:
                # Drop the low-graded chunks before reranking
                kept = [(g, r) for g, r in zip(graded, all_results) if g.grade != "low"]
                all_results = [r for _, r in kept]
        elif trace is not None:
            trace.note("grader", enabled=False)

        # --- 3. Rerank + (optional) MMR ---
        final = self._rerank_and_select(query, all_results, limit, trace)

        # --- 3b. Preview truncation (default) ---
        final = _apply_preview_mode(final, mode)

        # --- 4. Cache write-through ---
        if cache_on and final:
            self._cache.store(query, final, namespace=cache_namespace)

        # --- 5. Observability ---
        self._log_query(
            query, final, t_start, "search",
            {
                "cache_hit": False,
                "collections": target_collections,
                "n_candidates": len(all_results),
                "grader": bool(self._grader and self._grader.enabled),
                "rewriter": bool(self._rewriter and self._rewriter.enabled),
            },
            source,
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
        limit: int = 3,
        mode: str = "preview",
        trace: RetrievalTrace | None = None,
        source: str | None = None,
    ) -> list[CodeSearchResult]:
        """Search the single `mnemos_code` collection; scope to a project or
        any other slice via the `tags` payload filter rather than per-project
        collections."""
        t_start = time.perf_counter()
        dense_vec, sparse_vec = self._embed(query, trace, task="code")

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
        hits = self._query_collection("mnemos_code", dense_vec, sparse_vec, query_filter, per_collection, trace)

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
        if trace is not None:
            trace.pool("mnemos_code", hits, all_results)

        final = _apply_preview_mode(self._rerank_and_select(query, all_results, limit, trace), mode)
        self._log_query(
            query, final, t_start, "search_code",
            {
                "collections": ["mnemos_code"],
                "n_candidates": len(all_results),
                "tags_any": tags_any or [],
                "tags_all": tags_all or [],
            },
            source,
        )
        return final

    def search_skills(self, query: str, limit: int = 3, trace: RetrievalTrace | None = None,
                      source: str | None = None) -> list[SkillResult]:
        t_start = time.perf_counter()
        dense_vec, sparse_vec = self._embed(query, trace)
        per_collection = _HYBRID_TOP if (self._reranker and self._reranker.enabled) else limit
        hits = self._query_collection("mnemos_skills", dense_vec, sparse_vec, None, per_collection, trace)
        results = [
            SkillResult(
                skill_name=hit.payload.get("skill_name", ""),
                description=hit.payload.get("description", ""),
                score=hit.score,
                instructions_preview=hit.payload.get("content", "")[:200],
            )
            for hit in hits
        ]
        if trace is not None:
            trace.pool("mnemos_skills", hits, results)
        # Reranker uses .content; SkillResult has instructions_preview instead — wrap it.
        if self._reranker is not None and self._reranker.enabled and results:
            candidates = [(r.instructions_preview, r) for r in results]
            ranked = self._rerank(query, candidates, limit, trace)
            final = [s.payload for s in ranked]
        else:
            if trace is not None:
                trace.note("rerank", enabled=False)
            final = results[:limit]
        self._log_query(
            query, final, t_start, "search_skills",
            {"collections": ["mnemos_skills"], "n_candidates": len(results)},
            source,
        )
        return final

    def search_memory(
        self,
        query: str,
        tags_any: list[str] | None = None,
        tags_all: list[str] | None = None,
        memory_type: str | None = None,
        limit: int = 5,
        mode: str = "preview",
        trace: RetrievalTrace | None = None,
        source: str | None = None,
    ) -> list[MemoryResult]:
        dense_vec, sparse_vec = self._embed(query, trace)

        t_start = time.perf_counter()
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
        hits = self._query_collection("mnemos_memory", dense_vec, sparse_vec, query_filter, per_collection, trace)
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
        if trace is not None:
            trace.pool("mnemos_memory", hits, results)
        if self._reranker is not None and self._reranker.enabled and results:
            candidates = [(r.content, r) for r in results]
            ranked = self._rerank(query, candidates, limit, trace)
            final = _apply_preview_mode([s.payload for s in ranked], mode)
        else:
            if trace is not None:
                trace.note("rerank", enabled=False)
            final = _apply_preview_mode(results[:limit], mode)
        self._log_query(
            query, final, t_start, "search_memory",
            {
                "collections": ["mnemos_memory"],
                "n_candidates": len(results),
                "memory_type": memory_type or "",
            },
            source,
        )
        return final
