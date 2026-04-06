from __future__ import annotations

from qdrant_client import QdrantClient
from qdrant_client.models import Filter, FieldCondition, MatchValue, MatchAny

from rag_core.collections import COLLECTIONS
from rag_core.embeddings import EmbeddingService
from rag_core.models import (
    CodeSearchResult,
    MemoryResult,
    SearchResult,
    SkillResult,
)


class SearchService:
    def __init__(
        self,
        qdrant_client: QdrantClient,
        embedding_service: EmbeddingService,
    ) -> None:
        self._qdrant = qdrant_client
        self._embeddings = embedding_service

    def search(
        self,
        query: str,
        collections: list[str] | None = None,
        file_types: list[str] | None = None,
        path_filter: str | None = None,
        limit: int = 5,
    ) -> list[SearchResult]:
        vector = self._embeddings.embed(query)
        target_collections = collections or [
            c.name for c in COLLECTIONS if c.name != "mnemos_memory"
        ]

        all_results = []
        for coll_name in target_collections:
            must_conditions = []
            if file_types:
                must_conditions.append(
                    FieldCondition(key="language", match=MatchAny(any=file_types))
                )
            if path_filter:
                must_conditions.append(
                    FieldCondition(key="file_path", match=MatchValue(value=path_filter))
                )

            query_filter = Filter(must=must_conditions) if must_conditions else None

            hits = self._qdrant.query_points(
                collection_name=coll_name,
                query=vector,
                query_filter=query_filter,
                limit=limit,
            ).points
            for hit in hits:
                all_results.append(
                    SearchResult(
                        content=hit.payload.get("content", ""),
                        file_path=hit.payload.get("file_path", ""),
                        score=hit.score,
                        chunk_type=hit.payload.get("chunk_type", ""),
                        collection=coll_name,
                        metadata={
                            k: v
                            for k, v in hit.payload.items()
                            if k not in ("content", "file_path", "chunk_type")
                        },
                    )
                )

        all_results.sort(key=lambda r: r.score, reverse=True)
        return all_results[:limit]

    def search_code(
        self,
        query: str,
        language: str | None = None,
        symbol_type: str | None = None,
        project: str | None = None,
        path_filter: str | None = None,
        limit: int = 5,
    ) -> list[CodeSearchResult]:
        vector = self._embeddings.embed(query)

        if project:
            collections = [f"mnemos_code_{project}"]
        else:
            collections = [c.name for c in COLLECTIONS if c.name.startswith("mnemos_code_")]

        all_results = []
        for coll_name in collections:
            must_conditions = []
            if language:
                must_conditions.append(
                    FieldCondition(key="language", match=MatchValue(value=language))
                )
            if symbol_type:
                must_conditions.append(
                    FieldCondition(key="chunk_type", match=MatchValue(value=symbol_type))
                )
            if path_filter:
                must_conditions.append(
                    FieldCondition(key="file_path", match=MatchValue(value=path_filter))
                )

            query_filter = Filter(must=must_conditions) if must_conditions else None

            hits = self._qdrant.query_points(
                collection_name=coll_name,
                query=vector,
                query_filter=query_filter,
                limit=limit,
            ).points
            for hit in hits:
                all_results.append(
                    CodeSearchResult(
                        content=hit.payload.get("content", ""),
                        file_path=hit.payload.get("file_path", ""),
                        score=hit.score,
                        chunk_type=hit.payload.get("chunk_type", ""),
                        collection=coll_name,
                        metadata={},
                        language=hit.payload.get("language", ""),
                        symbol_name=hit.payload.get("symbol_name"),
                        package=hit.payload.get("package"),
                    )
                )

        all_results.sort(key=lambda r: r.score, reverse=True)
        return all_results[:limit]

    def search_skills(self, query: str, limit: int = 3) -> list[SkillResult]:
        vector = self._embeddings.embed(query)
        hits = self._qdrant.query_points(
            collection_name="mnemos_skills",
            query=vector,
            limit=limit,
        ).points
        return [
            SkillResult(
                skill_name=hit.payload.get("skill_name", ""),
                description=hit.payload.get("description", ""),
                score=hit.score,
                instructions_preview=hit.payload.get("content", "")[:200],
            )
            for hit in hits
        ]

    def search_memory(
        self,
        query: str,
        project: str | None = None,
        memory_type: str | None = None,
        limit: int = 5,
    ) -> list[MemoryResult]:
        vector = self._embeddings.embed(query)

        must_conditions = [
            FieldCondition(key="status", match=MatchValue(value="approved"))
        ]
        if project:
            must_conditions.append(
                FieldCondition(key="project", match=MatchValue(value=project))
            )
        if memory_type:
            must_conditions.append(
                FieldCondition(key="memory_type", match=MatchValue(value=memory_type))
            )

        hits = self._qdrant.query_points(
            collection_name="mnemos_memory",
            query=vector,
            query_filter=Filter(must=must_conditions),
            limit=limit,
        ).points
        return [
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
