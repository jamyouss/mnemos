from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from mcp.server import Server
from mcp.server.streamable_http_manager import StreamableHTTPSessionManager
from qdrant_client import QdrantClient
from starlette.responses import Response

from server.config import settings
from server.api import api_router
from core.embeddings import EmbeddingService
from core.indexer import Indexer


def create_app() -> FastAPI:
    _session_manager_holder: dict[str, StreamableHTTPSessionManager] = {}

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.qdrant = QdrantClient(
            host=settings.qdrant_host, port=settings.qdrant_port
        )
        app.state.embeddings = EmbeddingService(model_name=settings.embedding_model)

        # Reranker is constructed up-front with `enabled` flag wired through;
        # the heavyweight model load happens lazily on the first rerank call.
        from core.reranker import CrossEncoderReranker
        app.state.reranker = CrossEncoderReranker(
            model_name=settings.mnemos_reranker_model,
            model_type=settings.mnemos_reranker_type,
            enabled=settings.mnemos_reranker_enabled,
        )

        # Wait — search_service needs grader+rewriter which need the LLM provider,
        # so we defer construction until after the LLM is built (a few lines below).
        app.state.search_service = None  # placeholder; will be set below

        from core.memory_extractor import MemoryExtractor
        from core.deduplicator import Deduplicator
        from core.llm import LLMConfig, make_llm_provider
        from core.contextual import ContextualEnricher

        llm_base_url = settings.mnemos_llm_base_url or (
            settings.mnemos_ollama_url if settings.mnemos_llm_provider == "ollama" else ""
        )
        app.state.llm = make_llm_provider(
            LLMConfig(
                provider=settings.mnemos_llm_provider,
                model=settings.mnemos_llm_model,
                api_key=settings.mnemos_llm_api_key,
                base_url=llm_base_url,
            )
        )
        app.state.memory_extractor = MemoryExtractor(llm=app.state.llm)
        app.state.contextual = ContextualEnricher(
            llm=app.state.llm,
            enabled=settings.mnemos_contextual_enabled,
            workers=settings.mnemos_contextual_workers,
        )
        # Optional YAML override for path → tags mapping. Missing file → empty dict,
        # which means "cumulative path segments" everywhere by default.
        from core.projects import load_path_tags
        from pathlib import Path as _Path
        app.state.path_tags = load_path_tags(
            _Path(settings.mnemos_projects_config_path)
        )

        app.state.indexer = Indexer(
            qdrant_client=app.state.qdrant,
            embedding_service=app.state.embeddings,
            contextual_enricher=app.state.contextual,
            path_tags=app.state.path_tags,
            codebase_root=settings.codebase_path,
        )

        # CRAG components (Phase 3): grader + rewriter
        from core.grader import DocumentGrader
        from core.rewriter import QueryRewriter
        app.state.grader = DocumentGrader(
            llm=app.state.llm,
            enabled=settings.mnemos_grader_enabled,
            workers=settings.mnemos_grader_workers,
        )
        app.state.rewriter = QueryRewriter(
            llm=app.state.llm,
            enabled=settings.mnemos_rewriter_enabled,
            strategy=settings.mnemos_rewriter_strategy,  # type: ignore[arg-type]
            max_variants=settings.mnemos_rewriter_max_variants,
        )

        # Semantic router (Phase 4D): pre-computes collection-description embeddings.
        from core.router import QueryRouter
        app.state.router = QueryRouter(
            embedding_service=app.state.embeddings,
            enabled=settings.mnemos_router_enabled,
            top_k=settings.mnemos_router_top_k,
            min_score=settings.mnemos_router_min_score,
        )

        # Semantic cache (Phase 4E): cosine-similarity cache backed by Qdrant.
        from core.cache import SemanticCache
        app.state.cache = SemanticCache(
            qdrant_client=app.state.qdrant,
            embedding_service=app.state.embeddings,
            enabled=settings.mnemos_cache_enabled,
            threshold=settings.mnemos_cache_threshold,
            ttl_seconds=settings.mnemos_cache_ttl_seconds,
        )

        # Observability (Phase 9): per-query JSONL log.
        from core.observability import QueryLogger
        app.state.query_logger = QueryLogger(
            path=settings.mnemos_query_log_path,
            enabled=settings.mnemos_query_log_enabled,
        )

        # Now we can build the SearchService with all the wiring it needs.
        from server.search import SearchService
        app.state.search_service = SearchService(
            qdrant_client=app.state.qdrant,
            embedding_service=app.state.embeddings,
            reranker=app.state.reranker,
            mmr_enabled=settings.mnemos_mmr_enabled,
            mmr_lambda=settings.mnemos_mmr_lambda,
            grader=app.state.grader,
            rewriter=app.state.rewriter,
            router=app.state.router,
            cache=app.state.cache,
            query_logger=app.state.query_logger,
        )
        app.state.deduplicator = Deduplicator(
            qdrant_client=app.state.qdrant,
            embedding_service=app.state.embeddings,
            memory_extractor=app.state.memory_extractor,
            threshold=settings.mnemos_dedup_threshold,
            strategy=settings.mnemos_dedup_strategy,
        )

        # Ensure all collections exist on startup (creates new ones as hybrid;
        # legacy unnamed-dense collections stay untouched until `reindex --recreate`).
        from core.collections import COLLECTIONS
        for coll in COLLECTIONS:
            app.state.indexer.ensure_collection(coll.name, coll.vector_size)

        from server.mcp_tools import create_mcp_server
        mcp_server = create_mcp_server(
            search_service=app.state.search_service,
            indexer=app.state.indexer,
            qdrant_client=app.state.qdrant,
            embedding_service=app.state.embeddings,
            deduplicator=app.state.deduplicator,
        )

        # Streamable HTTP session manager
        session_manager = StreamableHTTPSessionManager(
            app=mcp_server,
            json_response=True,
        )
        _session_manager_holder["mgr"] = session_manager

        async with session_manager.run():
            yield

        app.state.qdrant.close()

    app = FastAPI(title="Mnemos MCP Server", lifespan=lifespan)

    # --- MCP Streamable HTTP transport ---
    async def mcp_http_handler(scope, receive, send):
        mgr = _session_manager_holder.get("mgr")
        if mgr is None:
            response = Response("MCP server not initialised", status_code=503)
            await response(scope, receive, send)
            return
        await mgr.handle_request(scope, receive, send)

    app.mount("/mcp", app=mcp_http_handler)

    # --- REST endpoints ---
    @app.get("/health")
    async def health():
        return {"status": "healthy"}

    app.include_router(api_router)

    return app


if __name__ == "__main__":
    import uvicorn
    app = create_app()
    uvicorn.run(app, host="0.0.0.0", port=8100)
