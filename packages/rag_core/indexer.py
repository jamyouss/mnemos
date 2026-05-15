from __future__ import annotations

import time
import uuid
from datetime import datetime, timezone

from qdrant_client import QdrantClient
from qdrant_client.models import (
    Distance,
    FieldCondition,
    Filter,
    FilterSelector,
    MatchValue,
    Modifier,
    PointStruct,
    SparseVectorParams,
    VectorParams,
)

from rag_core.chunkers.fallback_chunker import FallbackChunker
from rag_core.chunkers.go_chunker import GoChunker
from rag_core.chunkers.markdown_chunker import MarkdownChunker
from rag_core.chunkers.vue_chunker import VueChunker
from rag_core.collections import DENSE_VECTOR_NAME, SPARSE_VECTOR_NAME
from rag_core.contextual import ContextualEnricher
from rag_core.embeddings import EmbeddingService
from rag_core.sparse import bm25_sparse


class Indexer:
    def __init__(
        self,
        qdrant_client: QdrantClient,
        embedding_service: EmbeddingService,
        contextual_enricher: ContextualEnricher | None = None,
    ) -> None:
        self._qdrant = qdrant_client
        self._embeddings = embedding_service
        self._contextual = contextual_enricher
        self._go_chunker = GoChunker()
        self._vue_chunker = VueChunker()
        self._md_chunker = MarkdownChunker()
        self._fallback_chunker = FallbackChunker()

    def ensure_collection(self, collection_name: str, vector_size: int = 384) -> None:
        """Create the collection if missing, configured for hybrid (dense + sparse BM25).

        Collections created prior to the hybrid migration use a single unnamed
        dense vector; those need to be recreated explicitly via the migration
        path (delete + recreate) because Qdrant does not permit altering the
        vector schema in-place.
        """
        existing = {c.name for c in self._qdrant.get_collections().collections}
        if collection_name in existing:
            return

        self._qdrant.create_collection(
            collection_name=collection_name,
            vectors_config={
                DENSE_VECTOR_NAME: VectorParams(size=vector_size, distance=Distance.COSINE),
            },
            sparse_vectors_config={
                SPARSE_VECTOR_NAME: SparseVectorParams(modifier=Modifier.IDF),
            },
        )

    def is_hybrid(self, collection_name: str) -> bool:
        """Return True if the collection already uses named hybrid (dense + sparse) vectors."""
        try:
            info = self._qdrant.get_collection(collection_name)
        except Exception:
            return False
        vectors = info.config.params.vectors
        sparse = info.config.params.sparse_vectors
        return isinstance(vectors, dict) and DENSE_VECTOR_NAME in vectors and sparse is not None

    def recreate_collection_hybrid(self, collection_name: str, vector_size: int = 384) -> None:
        """Drop and recreate the collection with the hybrid schema. Destructive."""
        try:
            self._qdrant.delete_collection(collection_name=collection_name)
        except Exception:
            pass
        self.ensure_collection(collection_name, vector_size)

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

        # Optional Anthropic-style contextual enrichment: prepend an LLM-generated
        # one-sentence preamble to each chunk before embedding + sparse indexing.
        if self._contextual is not None and self._contextual.enabled:
            chunks = self._contextual.enrich(content, chunks, file_path)

        texts = [c["content"] for c in chunks]
        dense_vectors = self._embeddings.embed_batch(texts)
        sparse_vectors = [bm25_sparse(t) for t in texts]

        now = datetime.now(timezone.utc).isoformat()
        points = []
        for chunk, dense, sparse in zip(chunks, dense_vectors, sparse_vectors):
            point_id = self._make_point_id(file_path, chunk.get("chunk_index", 0))
            payload = {
                **chunk,
                "last_indexed_at": now,
                "file_mtime": file_mtime or time.time(),
            }
            points.append(
                PointStruct(
                    id=point_id,
                    vector={
                        DENSE_VECTOR_NAME: dense,
                        SPARSE_VECTOR_NAME: sparse,
                    },
                    payload=payload,
                )
            )

        # Batch upserts to stay under Qdrant payload size limits
        batch_size = 50
        for i in range(0, len(points), batch_size):
            self._qdrant.upsert(collection_name=collection, points=points[i:i + batch_size])
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
