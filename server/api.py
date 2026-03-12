from __future__ import annotations

import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel
from qdrant_client.models import (
    Distance,
    FieldCondition,
    Filter,
    FilterSelector,
    MatchValue,
    PointStruct,
    VectorParams,
)

from rag_core.collections import COLLECTIONS
from server.config import settings

api_router = APIRouter()

_VALID_COLLECTIONS = {c.name for c in COLLECTIONS}


# ---------------------------------------------------------------------------
# Request / response models
# ---------------------------------------------------------------------------


class ReindexInternalRequest(BaseModel):
    file_path: str
    event: str = "modified"
    collection: str


class IndexPushRequest(BaseModel):
    file_path: str
    collection: str
    content: str


class SearchRequest(BaseModel):
    query: str
    collections: Optional[List[str]] = None
    file_types: Optional[List[str]] = None
    path_filter: Optional[str] = None
    limit: int = 5


class SearchCodeRequest(BaseModel):
    query: str
    language: Optional[str] = None
    symbol_type: Optional[str] = None
    project: Optional[str] = None
    path_filter: Optional[str] = None
    limit: int = 5


class SearchSkillsRequest(BaseModel):
    query: str
    limit: int = 3


class ReindexRequest(BaseModel):
    collection: str
    path: Optional[str] = None
    full: bool = False


class MemoryCreateRequest(BaseModel):
    content: str
    project: Optional[str] = None
    memory_type: str = "general"
    tags: List[str] = []
    status: str = "pending"


class MemoryReviewRequest(BaseModel):
    action: str  # "approve" or "reject"


# ---------------------------------------------------------------------------
# Internal endpoints (used by watcher)
# ---------------------------------------------------------------------------

_ALLOWED_BASE_DIRS = [
    settings.codebase_path,
    settings.claude_config_path,
]


def _validate_path(file_path: str) -> Path:
    """Resolve path and ensure it starts with an allowed base directory."""
    resolved = Path(file_path).resolve()
    for base in _ALLOWED_BASE_DIRS:
        try:
            resolved.relative_to(Path(base).resolve())
            return resolved
        except ValueError:
            continue
    raise HTTPException(
        status_code=400,
        detail=f"Path traversal detected: '{file_path}' is outside allowed directories.",
    )


@api_router.post("/internal/reindex")
async def internal_reindex(body: ReindexInternalRequest, request: Request):
    safe_path = _validate_path(body.file_path)

    if body.event == "deleted":
        request.app.state.indexer.delete_file(
            file_path=body.file_path,
            collection=body.collection,
        )
        return {"status": "deleted", "file_path": body.file_path}

    if not safe_path.exists():
        raise HTTPException(status_code=404, detail=f"File not found: {body.file_path}")

    content = safe_path.read_text(encoding="utf-8")
    request.app.state.indexer.ensure_collection(body.collection)
    count = request.app.state.indexer.index_file(
        content=content,
        file_path=body.file_path,
        collection=body.collection,
        file_mtime=safe_path.stat().st_mtime,
    )
    return {"status": "indexed", "file_path": body.file_path, "chunks": count}


# ---------------------------------------------------------------------------
# Push API (deployed mode)
# ---------------------------------------------------------------------------


@api_router.post("/api/index")
async def push_index(body: IndexPushRequest, request: Request):
    if body.collection not in _VALID_COLLECTIONS:
        raise HTTPException(status_code=400, detail=f"Unknown collection: {body.collection}")
    request.app.state.indexer.ensure_collection(body.collection)
    count = request.app.state.indexer.index_file(
        content=body.content,
        file_path=body.file_path,
        collection=body.collection,
    )
    return {"status": "indexed", "file_path": body.file_path, "chunks": count}


@api_router.delete("/api/index/{collection}/{file_path:path}")
async def delete_index(collection: str, file_path: str, request: Request):
    request.app.state.indexer.delete_file(
        file_path=file_path,
        collection=collection,
    )
    return {"status": "deleted", "collection": collection, "file_path": file_path}


# ---------------------------------------------------------------------------
# Search API (used by CLI)
# ---------------------------------------------------------------------------


@api_router.post("/api/search")
async def search(body: SearchRequest, request: Request):
    results = request.app.state.search_service.search(
        query=body.query,
        collections=body.collections,
        file_types=body.file_types,
        path_filter=body.path_filter,
        limit=body.limit,
    )
    return {"results": [r.model_dump() for r in results]}


@api_router.post("/api/search-code")
async def search_code(body: SearchCodeRequest, request: Request):
    results = request.app.state.search_service.search_code(
        query=body.query,
        language=body.language,
        symbol_type=body.symbol_type,
        project=body.project,
        path_filter=body.path_filter,
        limit=body.limit,
    )
    return {"results": [r.model_dump() for r in results]}


@api_router.post("/api/search-skills")
async def search_skills(body: SearchSkillsRequest, request: Request):
    results = request.app.state.search_service.search_skills(
        query=body.query,
        limit=body.limit,
    )
    return {"results": [r.model_dump() for r in results]}


# ---------------------------------------------------------------------------
# Reindex API (used by CLI)
# ---------------------------------------------------------------------------


@api_router.post("/api/reindex")
async def reindex(body: ReindexRequest, request: Request):
    indexer = request.app.state.indexer
    indexer.ensure_collection(body.collection)

    base_path = _validate_path(body.path) if body.path else None
    indexed = 0

    if base_path is not None:
        files = list(base_path.rglob("*")) if body.full else [base_path]
        for fp in files:
            if fp.is_file():
                try:
                    content = fp.read_text(encoding="utf-8")
                    indexed += indexer.index_file(
                        content=content,
                        file_path=str(fp),
                        collection=body.collection,
                        file_mtime=fp.stat().st_mtime,
                    )
                except (UnicodeDecodeError, OSError):
                    pass

    return {"status": "reindexed", "collection": body.collection, "chunks_indexed": indexed}


# ---------------------------------------------------------------------------
# Memory API (used by CLI)
# ---------------------------------------------------------------------------

_MEMORY_COLLECTION = "rag_memory"
_MEMORY_VECTOR_SIZE = 384


def _ensure_memory_collection(qdrant, vector_size: int = _MEMORY_VECTOR_SIZE) -> None:
    existing = {c.name for c in qdrant.get_collections().collections}
    if _MEMORY_COLLECTION not in existing:
        qdrant.create_collection(
            collection_name=_MEMORY_COLLECTION,
            vectors_config=VectorParams(size=vector_size, distance=Distance.COSINE),
        )


@api_router.get("/api/memory")
async def list_memory(request: Request, status: Optional[str] = None):
    qdrant = request.app.state.qdrant

    scroll_filter = None
    if status:
        scroll_filter = Filter(
            must=[FieldCondition(key="status", match=MatchValue(value=status))]
        )

    records, _ = qdrant.scroll(
        collection_name=_MEMORY_COLLECTION,
        scroll_filter=scroll_filter,
        limit=100,
        with_payload=True,
        with_vectors=False,
    )
    return {"entries": [r.payload for r in records]}


@api_router.post("/api/memory")
async def create_memory(body: MemoryCreateRequest, request: Request):
    qdrant = request.app.state.qdrant
    embeddings = request.app.state.embeddings

    _ensure_memory_collection(qdrant)

    mem_id = str(uuid.uuid4())
    vector = embeddings.embed(body.content)
    now = datetime.now(timezone.utc).isoformat()

    payload = {
        "id": mem_id,
        "content": body.content,
        "project": body.project,
        "memory_type": body.memory_type,
        "tags": body.tags,
        "status": body.status,
        "created_at": now,
    }

    point_id = str(uuid.uuid5(uuid.NAMESPACE_URL, mem_id))
    qdrant.upsert(
        collection_name=_MEMORY_COLLECTION,
        points=[PointStruct(id=point_id, vector=vector, payload=payload)],
    )
    return {"status": "created", "id": mem_id}


@api_router.post("/api/memory/{mem_id}/review")
async def review_memory(mem_id: str, body: MemoryReviewRequest, request: Request):
    if body.action not in ("approve", "reject"):
        raise HTTPException(status_code=400, detail="action must be 'approve' or 'reject'")

    qdrant = request.app.state.qdrant
    new_status = "approved" if body.action == "approve" else "rejected"

    point_id = str(uuid.uuid5(uuid.NAMESPACE_URL, mem_id))

    # Fetch existing record to update payload
    results, _ = qdrant.scroll(
        collection_name=_MEMORY_COLLECTION,
        scroll_filter=Filter(
            must=[FieldCondition(key="id", match=MatchValue(value=mem_id))]
        ),
        limit=1,
        with_payload=True,
        with_vectors=True,
    )

    if not results:
        raise HTTPException(status_code=404, detail=f"Memory entry '{mem_id}' not found.")

    record = results[0]
    updated_payload = {**record.payload, "status": new_status}

    qdrant.upsert(
        collection_name=_MEMORY_COLLECTION,
        points=[
            PointStruct(
                id=point_id,
                vector=record.vector,
                payload=updated_payload,
            )
        ],
    )
    return {"status": new_status, "id": mem_id}


# ---------------------------------------------------------------------------
# Status API
# ---------------------------------------------------------------------------


@api_router.get("/api/status")
async def api_status(request: Request):
    qdrant = request.app.state.qdrant
    collections = qdrant.get_collections().collections
    index_info = {}
    for coll in collections:
        try:
            info = qdrant.get_collection(coll.name)
            index_info[coll.name] = {
                "vectors_count": info.vectors_count,
                "points_count": info.points_count,
                "status": str(info.status),
            }
        except Exception:
            index_info[coll.name] = {"error": "unavailable"}

    return {"status": "healthy", "collections": index_info}
