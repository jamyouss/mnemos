from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from mcp.server import Server
from mcp.server.sse import SseServerTransport
from qdrant_client import QdrantClient
from starlette.requests import Request
from starlette.responses import Response
from starlette.routing import Mount, Route

from server.config import settings
from server.api import api_router
from rag_core.embeddings import EmbeddingService
from rag_core.indexer import Indexer


def create_app() -> FastAPI:
    # Placeholder mcp_server; replaced in lifespan once real services are up.
    # This allows the SSE route to be registered before lifespan runs.
    _mcp_server_holder: dict[str, Server] = {}

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
        )
        app.state.mcp_server = mcp_server
        _mcp_server_holder["server"] = mcp_server
        yield
        app.state.qdrant.close()

    app = FastAPI(title="RAG MCP Server", lifespan=lifespan)

    # --- MCP SSE transport ---
    sse_transport = SseServerTransport("/mcp/messages/")

    async def handle_sse(request: Request) -> Response:
        mcp_server = _mcp_server_holder.get("server")
        if mcp_server is None:
            return Response("MCP server not initialised", status_code=503)
        async with sse_transport.connect_sse(
            request.scope, request.receive, request._send
        ) as streams:
            await mcp_server.run(
                streams[0],
                streams[1],
                mcp_server.create_initialization_options(),
            )
        return Response()

    app.mount(
        "/mcp",
        app=_build_mcp_starlette_app(sse_transport, handle_sse),
    )

    # --- REST endpoints ---
    @app.get("/health")
    async def health():
        return {"status": "healthy"}

    app.include_router(api_router)

    return app


def _build_mcp_starlette_app(
    sse_transport: SseServerTransport,
    handle_sse,
):
    """Return a minimal Starlette ASGI app with SSE + message routes."""
    from starlette.applications import Starlette

    routes = [
        Route("/sse", endpoint=handle_sse, methods=["GET"]),
        Mount("/messages/", app=sse_transport.handle_post_message),
    ]
    return Starlette(routes=routes)


if __name__ == "__main__":
    import uvicorn
    app = create_app()
    uvicorn.run(app, host="0.0.0.0", port=8100)
