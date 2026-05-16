"""Deduplicate memories by detecting similar entries and merging or replacing."""
from __future__ import annotations

import time
import uuid
import logging
from datetime import datetime, timezone

from qdrant_client import QdrantClient
from qdrant_client.models import PointStruct

from core.collections import DENSE_VECTOR_NAME, SPARSE_VECTOR_NAME
from core.embeddings import EmbeddingService
from core.models import DeduplicationResult, ExtractedMemory
from core.sparse import bm25_sparse

logger = logging.getLogger("mnemos.deduplicator")

_MEMORY_COLLECTION = "mnemos_memory"


class Deduplicator:
    def __init__(
        self,
        qdrant_client: QdrantClient,
        embedding_service: EmbeddingService,
        memory_extractor,
        threshold: float = 0.85,
        strategy: str = "merge",
    ) -> None:
        self._qdrant = qdrant_client
        self._embeddings = embedding_service
        self._extractor = memory_extractor
        self._threshold = threshold
        self._strategy = strategy

    def deduplicate_and_store(self, memory: ExtractedMemory, status: str = "pending") -> DeduplicationResult:
        vector = self._embeddings.embed(memory.content)

        try:
            hits = self._qdrant.query_points(
                collection_name=_MEMORY_COLLECTION,
                query=vector,
                using=DENSE_VECTOR_NAME,
                limit=1,
            ).points
        except Exception:
            # Legacy unnamed-dense fallback
            hits = self._qdrant.query_points(
                collection_name=_MEMORY_COLLECTION,
                query=vector,
                limit=1,
            ).points

        if hits and hits[0].score >= self._threshold:
            existing = hits[0]
            if self._strategy == "merge":
                return self._merge(existing, memory, vector, status)
            else:
                return self._replace(existing, memory, vector, status)

        return self._insert(memory, vector, status)

    def _named_vector(self, dense: list[float], text: str) -> dict:
        return {
            DENSE_VECTOR_NAME: dense,
            SPARSE_VECTOR_NAME: bm25_sparse(text),
        }

    def _insert(self, memory: ExtractedMemory, vector: list[float], status: str) -> DeduplicationResult:
        mem_id = str(uuid.uuid4())
        point_id = str(uuid.uuid5(uuid.NAMESPACE_URL, mem_id))
        now = datetime.now(timezone.utc).isoformat()

        payload = {
            "id": mem_id,
            "content": memory.content,
            "project": memory.project,
            "memory_type": memory.memory_type,
            "tags": memory.tags,
            "status": status,
            "created_at": now,
            "file_path": f"memory/{mem_id}",
            "chunk_type": "memory",
            "last_indexed_at": now,
            "file_mtime": time.time(),
        }

        self._qdrant.upsert(
            collection_name=_MEMORY_COLLECTION,
            points=[
                PointStruct(
                    id=point_id,
                    vector=self._named_vector(vector, memory.content),
                    payload=payload,
                )
            ],
        )
        return DeduplicationResult(action="inserted", memory_id=mem_id)

    def _merge(self, existing, memory: ExtractedMemory, vector: list[float], status: str) -> DeduplicationResult:
        existing_id = existing.payload.get("id", "")
        existing_content = existing.payload.get("content", "")

        merged_content = self._extractor.merge_memories(existing_content, memory.content)
        merged_vector = self._embeddings.embed(merged_content)
        now = datetime.now(timezone.utc).isoformat()

        updated_payload = {
            **existing.payload,
            "content": merged_content,
            "tags": list(set(existing.payload.get("tags", []) + memory.tags)),
            "last_indexed_at": now,
            "file_mtime": time.time(),
        }

        self._qdrant.upsert(
            collection_name=_MEMORY_COLLECTION,
            points=[
                PointStruct(
                    id=existing.id,
                    vector=self._named_vector(merged_vector, merged_content),
                    payload=updated_payload,
                )
            ],
        )
        return DeduplicationResult(action="merged", memory_id=existing_id, merged_with=existing_id)

    def _replace(self, existing, memory: ExtractedMemory, vector: list[float], status: str) -> DeduplicationResult:
        existing_id = existing.payload.get("id", "")

        self._qdrant.delete(
            collection_name=_MEMORY_COLLECTION,
            points_selector=[existing.id],
        )

        result = self._insert(memory, vector, status)
        return DeduplicationResult(
            action="replaced",
            memory_id=result.memory_id,
            merged_with=existing_id,
        )
