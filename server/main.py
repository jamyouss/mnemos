from __future__ import annotations

import logging
import threading
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from mcp.server import Server
from mcp.server.streamable_http_manager import StreamableHTTPSessionManager
from qdrant_client import QdrantClient
from starlette.responses import Response
from starlette.routing import Route

from fastapi import HTTPException
from fastapi.staticfiles import StaticFiles

from server.api import api_router
from server.audit import AUDIT_COLLECTION, AUDIT_FIELDS, audit_refusals, audit_router
from server.auth import BearerAuthMiddleware, TenantTable
from server.config import settings
from server.config_api import config_router
from server.config_bootstrap import bootstrap_config
from server.config_sync import ConfigSync, apply_projects
from server.connectors_api import connectors_router
from server.indexing import INDEX_EVENT_FIELDS, INDEX_EVENTS_COLLECTION, indexing_router
from server.projects_api import projects_router
from server.settings_store import ConfigStore
from server.tenants_api import tenants_router

logger = logging.getLogger("mnemos.server")
from core.config_store import ConfigDocStore
from core.embeddings import EmbeddingService
from core.event_log import EventLog
from core.indexer import Indexer


def _prune_forever(logs: list[EventLog], stop: threading.Event, every: float = 3600.0) -> None:
    """Drop what each log keeps past its retention, hourly. Every pod may run it."""
    while True:
        for log in logs:
            try:
                log.prune()
            except Exception:
                logger.warning("prune of %s failed", log.collection, exc_info=True)
        if stop.wait(every):
            return


def _backfill_path_fields(indexer: Indexer, collections: list[str]) -> None:
    """Label chunks indexed before `path_dirs`, so path filters see them without a reindex."""
    for name in collections:
        try:
            files = indexer.backfill_path_fields(name)
        except Exception:
            logger.exception("path_dirs backfill failed on %s", name)
            continue
        if files:
            logger.info("path_dirs backfill: %d files of %s", files, name)


def create_app() -> FastAPI:
    _session_manager_holder: dict[str, StreamableHTTPSessionManager] = {}
    # Filled from the database in the lifespan, replaced by the config sync.
    tenant_table = TenantTable()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.qdrant = QdrantClient(
            host=settings.qdrant_host,
            port=settings.qdrant_port,
            api_key=settings.qdrant_api_key or None,
            https=settings.qdrant_https,
        )
        # Shared configuration first: the settings overrides it holds take
        # precedence over env vars, and every component below is built from them.
        app.state.config_docs = ConfigDocStore(app.state.qdrant)
        app.state.config_docs.ensure_collection()
        bootstrap_config(app.state.config_docs, settings)
        app.state.config_store = ConfigStore(settings, app.state.config_docs)
        projects_doc = app.state.config_docs.get("projects")
        tenants_doc = app.state.config_docs.get("tenants")
        connectors_doc = app.state.config_docs.get("connectors")

        # Who changed what (Activity page) and what the index went through
        # (Indexing page): shared by every pod, pruned hourly.
        app.state.audit = EventLog(app.state.qdrant, AUDIT_COLLECTION, AUDIT_FIELDS,
                                   settings.mnemos_audit_retention_days)
        app.state.index_events = EventLog(app.state.qdrant, INDEX_EVENTS_COLLECTION, INDEX_EVENT_FIELDS,
                                          settings.mnemos_index_events_retention_days)
        for log in (app.state.audit, app.state.index_events):
            log.ensure_collection()

        app.state.embeddings = EmbeddingService(model_name=settings.embedding_model)

        # Reranker is constructed up-front with `enabled` flag wired through;
        # the heavyweight model load happens lazily on the first rerank call.
        from core.reranker import CrossEncoderReranker
        app.state.reranker = CrossEncoderReranker(
            model_name=settings.mnemos_reranker_model,
            model_type=settings.mnemos_reranker_type,
            enabled=settings.mnemos_reranker_enabled,
            max_length=settings.mnemos_reranker_max_length or None,
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
        app.state.indexer = Indexer(
            qdrant_client=app.state.qdrant,
            embedding_service=app.state.embeddings,
            contextual_enricher=app.state.contextual,
            only_declared_paths=settings.mnemos_index_only_declared_paths,
            codebase_root=settings.codebase_path,
            max_chunk_chars=settings.mnemos_max_chunk_chars,
        )
        # Path → tags and per-prefix ignore rules. No document → empty mapping,
        # which means "cumulative path segments" everywhere by default.
        apply_projects(app.state, projects_doc.data if projects_doc else {})

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
            cache=app.state.cache,
            query_logger=app.state.query_logger,
            dense_weight=settings.mnemos_fusion_dense_weight,
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
        # A collection sized for another embedding model would fail every
        # query, so refuse to start instead and name the way out.
        from core.collections import COLLECTIONS
        from core.indexer import dense_vector_size
        dim = app.state.embeddings.dimension
        for coll in COLLECTIONS:
            app.state.indexer.ensure_collection(coll.name)
            size = dense_vector_size(app.state.qdrant, coll.name)
            if size != dim:
                raise RuntimeError(
                    f"{coll.name} holds {size}-d vectors but {settings.embedding_model} "
                    f"produces {dim}-d. Migrate with `python -m core.embedding_migration "
                    f"sync --model {settings.embedding_model}` then `switch`, "
                    f"or set EMBEDDING_MODEL back."
                )

        # Off the startup path: one set_payload per file of an older index.
        threading.Thread(
            target=_backfill_path_fields,
            args=(app.state.indexer, [c.name for c in COLLECTIONS if c.name != "mnemos_memory"]),
            name="path-backfill",
            daemon=True,
        ).start()

        from server.mcp_tools import create_mcp_server
        mcp_server = create_mcp_server(
            search_service=app.state.search_service,
            indexer=app.state.indexer,
            qdrant_client=app.state.qdrant,
            embedding_service=app.state.embeddings,
            deduplicator=app.state.deduplicator,
            cache=app.state.cache,
            audit_log=app.state.audit,
            index_log=app.state.index_events,
        )

        # Streamable HTTP session manager
        session_manager = StreamableHTTPSessionManager(
            app=mcp_server,
            json_response=True,
        )
        _session_manager_holder["mgr"] = session_manager

        tenant_table.replace(tenants_doc.data if tenants_doc else {})
        config_store = app.state.config_store
        sync = ConfigSync(app.state.config_docs, settings.mnemos_config_poll_seconds)
        sync.register(
            "settings", lambda doc: config_store.apply_doc(doc, app.state), config_store.revision,
        )
        sync.register(
            "projects", lambda doc: apply_projects(app.state, doc.data),
            projects_doc.revision if projects_doc else 0,
        )
        sync.register(
            "tenants", lambda doc: tenant_table.replace(doc.data),
            tenants_doc.revision if tenants_doc else 0,
        )
        sync.register("connectors", None, connectors_doc.revision if connectors_doc else 0)
        app.state.config_sync = sync
        sync.start()

        # Started last: a startup that fails above leaves no thread behind.
        pruning = threading.Event()
        threading.Thread(target=_prune_forever, args=([app.state.audit, app.state.index_events], pruning),
                         name="event-log-prune", daemon=True).start()

        try:
            async with session_manager.run():
                yield
        finally:
            sync.stop()
            pruning.set()
            app.state.qdrant.close()

    app = FastAPI(title="Mnemos MCP Server", lifespan=lifespan)
    app.state.tenant_table = tenant_table

    if settings.mnemos_auth_enabled:
        # Whoever reaches an open Qdrant reads and rewrites every collection,
        # API keys or not: refuse to pretend the instance is protected.
        if not settings.qdrant_api_key:
            raise RuntimeError(
                "MNEMOS_AUTH_ENABLED=true needs QDRANT_API_KEY: start Qdrant with "
                "QDRANT__SERVICE__API_KEY and give the server the same key"
            )
        # Empty until the lifespan loads the tenants: nothing gets through before.
        app.add_middleware(BearerAuthMiddleware, tenants=tenant_table)

    # A 403 on a route that changes something lands in the audit trail.
    app.add_exception_handler(HTTPException, audit_refusals)

    # --- MCP Streamable HTTP transport ---
    async def mcp_http_handler(scope, receive, send):
        mgr = _session_manager_holder.get("mgr")
        if mgr is None:
            response = Response("MCP server not initialised", status_code=503)
            await response(scope, receive, send)
            return
        await mgr.handle_request(scope, receive, send)

    app.mount("/mcp", app=mcp_http_handler)

    class _McpWithoutSlash:
        """Bare `/mcp`: the Mount only matches `/mcp/…`, and the dashboard
        mounted at `/` would answer it with 405/404 instead of a redirect."""

        async def __call__(self, scope, receive, send):
            await mcp_http_handler(scope, receive, send)

    # A class instance, not a function: Starlette then routes it as raw ASGI.
    app.router.routes.append(Route("/mcp", endpoint=_McpWithoutSlash(), methods=["GET", "POST", "DELETE"]))

    # --- REST endpoints ---
    @app.get("/health")
    async def health():
        return {"status": "healthy"}

    # Instance theme. Not under /api, so it stays outside auth (it is CSS),
    # and registered before the root static mount, which would shadow it.
    # Read per request: editing the file needs a reload, not a restart.
    @app.get("/theme.css")
    def theme_css():
        try:
            css = Path(settings.mnemos_ui_theme_path).read_text(encoding="utf-8")
        except OSError:
            # Absent is the normal case: no instance override.
            css = ""
        return Response(css, media_type="text/css", headers={"Cache-Control": "no-cache"})

    app.include_router(api_router)
    app.include_router(config_router)
    app.include_router(connectors_router)
    app.include_router(projects_router)
    app.include_router(tenants_router)
    app.include_router(audit_router)
    app.include_router(indexing_router)

    # --- Dashboard ---
    # Mounted last and at the root, so it only catches what /mcp, /health and
    # /api did not. `html=True` serves index.html for unknown paths, which is
    # what a client-side router needs on a hard refresh.
    #
    # Optional on purpose: a server built without the UI stage still starts,
    # it just has no dashboard.
    ui_dir = Path(settings.mnemos_ui_path)
    if ui_dir.is_dir():
        app.mount("/", StaticFiles(directory=str(ui_dir), html=True), name="ui")
    else:
        logger.info("No dashboard at %s — serving the API only", ui_dir)

    return app


if __name__ == "__main__":
    import uvicorn
    app = create_app()
    uvicorn.run(app, host="0.0.0.0", port=8100)
