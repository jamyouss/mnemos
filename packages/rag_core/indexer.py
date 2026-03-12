from __future__ import annotations

import time
import uuid
from datetime import datetime, timezone

from qdrant_client import QdrantClient
from qdrant_client.models import Distance, PointStruct, VectorParams, Filter, FieldCondition, MatchValue, FilterSelector

from rag_core.chunkers.fallback_chunker import FallbackChunker
from rag_core.chunkers.go_chunker import GoChunker
from rag_core.chunkers.markdown_chunker import MarkdownChunker
from rag_core.chunkers.vue_chunker import VueChunker
from rag_core.embeddings import EmbeddingService


class Indexer:
    def __init__(
        self,
        qdrant_client: QdrantClient,
        embedding_service: EmbeddingService,
    ) -> None:
        self._qdrant = qdrant_client
        self._embeddings = embedding_service
        self._go_chunker = GoChunker()
        self._vue_chunker = VueChunker()
        self._md_chunker = MarkdownChunker()
        self._fallback_chunker = FallbackChunker()

    def ensure_collection(self, collection_name: str, vector_size: int = 384) -> None:
        existing = {
            c.name for c in self._qdrant.get_collections().collections
        }
        if collection_name not in existing:
            self._qdrant.create_collection(
                collection_name=collection_name,
                vectors_config=VectorParams(
                    size=vector_size, distance=Distance.COSINE
                ),
            )

    def index_file(
        self,
        content: str,
        file_path: str,
        collection: str,
        file_mtime: float | None = None,
    ) -> int:
        self.delete_file(file_path, collection)

        chunker = self._select_chunker(file_path)
        chunks = chunker.chunk(content, file_path)

        if not chunks:
            return 0

        texts = [c["content"] for c in chunks]
        vectors = self._embeddings.embed_batch(texts)

        now = datetime.now(timezone.utc).isoformat()
        points = []
        for chunk, vector in zip(chunks, vectors):
            point_id = self._make_point_id(file_path, chunk.get("chunk_index", 0))
            payload = {
                **chunk,
                "last_indexed_at": now,
                "file_mtime": file_mtime or time.time(),
            }
            points.append(PointStruct(id=point_id, vector=vector, payload=payload))

        self._qdrant.upsert(collection_name=collection, points=points)
        return len(points)

    def delete_file(self, file_path: str, collection: str) -> None:
        self._qdrant.delete(
            collection_name=collection,
            points_selector=FilterSelector(
                filter=Filter(
                    must=[FieldCondition(key="file_path", match=MatchValue(value=file_path))]
                )
            ),
        )

    def _select_chunker(self, file_path: str):
        if file_path.endswith(".go"):
            return self._go_chunker
        if file_path.endswith(".vue"):
            return self._vue_chunker
        if file_path.endswith(".md"):
            return self._md_chunker
        return self._fallback_chunker

    @staticmethod
    def _make_point_id(file_path: str, chunk_index: int) -> str:
        raw = f"{file_path}::{chunk_index}"
        return str(uuid.uuid5(uuid.NAMESPACE_URL, raw))
