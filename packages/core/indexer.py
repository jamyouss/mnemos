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
    KeywordIndexParams,
    MatchValue,
    Modifier,
    PayloadSchemaType,
    PointStruct,
    SparseVectorParams,
    VectorParams,
)

from core.chunkers.fallback_chunker import FallbackChunker
from core.chunkers.go_chunker import GoChunker
from core.chunkers.markdown_chunker import MarkdownChunker
from core.chunkers.vue_chunker import VueChunker
from core.collections import DENSE_VECTOR_NAME, PROJECT_PAYLOAD_FIELD, SPARSE_VECTOR_NAME
from core.contextual import ContextualEnricher
from core.embeddings import EmbeddingService
from core.projects import ProjectOverrides, detect_project
from core.sparse import bm25_sparse

# Collections whose payload is filtered by `project` at query time. We
# create the payload index on those at startup so the filter scales.
_PROJECT_INDEXED_COLLECTIONS = ("mnemos_code", "mnemos_memory")


class Indexer:
    def __init__(
        self,
        qdrant_client: QdrantClient,
        embedding_service: EmbeddingService,
        contextual_enricher: ContextualEnricher | None = None,
        project_overrides: ProjectOverrides | None = None,
        codebase_root: str = "/data/codebase",
    ) -> None:
        self._qdrant = qdrant_client
        self._embeddings = embedding_service
        self._contextual = contextual_enricher
        self._project_overrides = project_overrides or {}
        self._codebase_root = codebase_root.rstrip("/")
        self._go_chunker = GoChunker()
        self._vue_chunker = VueChunker()
        self._md_chunker = MarkdownChunker()
        self._fallback_chunker = FallbackChunker()

    def ensure_collection(self, collection_name: str, vector_size: int = 384) -> None:
        """Create the collection if missing, configured for hybrid (dense + sparse BM25).

        For collections that scope by project (mnemos_code, mnemos_memory) we
        also create a keyword payload index on the `project` field so that
        per-project filters stay O(log n).
        """
        existing = {c.name for c in self._qdrant.get_collections().collections}
        if collection_name not in existing:
            self._qdrant.create_collection(
                collection_name=collection_name,
                vectors_config={
                    DENSE_VECTOR_NAME: VectorParams(size=vector_size, distance=Distance.COSINE),
                },
                sparse_vectors_config={
                    SPARSE_VECTOR_NAME: SparseVectorParams(modifier=Modifier.IDF),
                },
            )

        if collection_name in _PROJECT_INDEXED_COLLECTIONS:
            self._ensure_project_payload_index(collection_name)

    def _ensure_project_payload_index(self, collection_name: str) -> None:
        """Idempotently create a keyword payload index on the `project` field."""
        try:
            self._qdrant.create_payload_index(
                collection_name=collection_name,
                field_name=PROJECT_PAYLOAD_FIELD,
                field_schema=KeywordIndexParams(type=PayloadSchemaType.KEYWORD),
            )
        except Exception:
            # Qdrant raises if the index already exists; that's fine.
            pass

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

    def _resolve_project(self, file_path: str, override: str | None) -> str | None:
        """Decide which `project` value to write into a chunk's payload.

        Order:
            1. explicit `override` (CLI flag, push API field, hook env var)
            2. YAML / first-segment detection on the path relative to the codebase root
        """
        if override:
            return override
        # Strip the codebase mount root before passing to detect_project so the
        # rules can be written in terms of paths the user actually sees.
        rel = file_path
        if file_path.startswith(self._codebase_root + "/"):
            rel = file_path[len(self._codebase_root) + 1:]
        return detect_project(rel, overrides=self._project_overrides)

    def index_file(
        self,
        content: str,
        file_path: str,
        collection: str,
        file_mtime: float | None = None,
        project: str | None = None,
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

        # Resolve project once per file. Only relevant for the code/memory
        # collections; skills/docs leave the field unset.
        resolved_project = self._resolve_project(file_path, project) \
            if collection in _PROJECT_INDEXED_COLLECTIONS else None

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
            if resolved_project is not None:
                payload[PROJECT_PAYLOAD_FIELD] = resolved_project
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
