from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from qdrant_client import QdrantClient

from server.config import settings
from rag_core.embeddings import EmbeddingService
from rag_core.indexer import Indexer


def create_app() -> FastAPI:
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
        yield
        app.state.qdrant.close()

    app = FastAPI(title="RAG MCP Server", lifespan=lifespan)

    @app.get("/health")
    async def health():
        return {"status": "healthy"}

    return app


if __name__ == "__main__":
    import uvicorn
    app = create_app()
    uvicorn.run(app, host="0.0.0.0", port=8100)
