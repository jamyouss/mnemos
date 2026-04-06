from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from mcp.server import Server
from mcp.server.streamable_http_manager import StreamableHTTPSessionManager
from qdrant_client import QdrantClient
from starlette.responses import Response

from server.config import settings
from server.api import api_router
from rag_core.embeddings import EmbeddingService
from rag_core.indexer import Indexer


def create_app() -> FastAPI:
    _session_manager_holder: dict[str, StreamableHTTPSessionManager] = {}

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.qdrant = QdrantClient(
            host=settings.qdrant_host, port=settings.qdrant_port
        )
        app.state.embeddings = EmbeddingService(model_name=settings.embedding_model)
        app.state.indexer = Indexer(
            qdrant_client=app.state.qdrant,
            embedding_service=app.state.embeddings,
        )
        from server.search import SearchService
        app.state.search_service = SearchService(
            qdrant_client=app.state.qdrant,
            embedding_service=app.state.embeddings,
        )

        from rag_core.memory_extractor import MemoryExtractor
        from rag_core.deduplicator import Deduplicator

        app.state.memory_extractor = MemoryExtractor(
            ollama_url=settings.mnemos_ollama_url,
            model=settings.mnemos_llm_model,
        )
        app.state.deduplicator = Deduplicator(
            qdrant_client=app.state.qdrant,
            embedding_service=app.state.embeddings,
            memory_extractor=app.state.memory_extractor,
            threshold=settings.mnemos_dedup_threshold,
            strategy=settings.mnemos_dedup_strategy,
        )

        # Ensure all collections exist on startup
        from rag_core.collections import COLLECTIONS
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
