"""The dashboard's Index page: browse what is indexed, read a file's chunks,
and ask why a path is (or is not) indexed.

Reads only. Reindexing and removing go through the existing routes
(`POST /api/reindex`, `DELETE /api/index/...`), which audit and track them.
Tagged collections show a key only what it may read.
"""
from __future__ import annotations

import os
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, Field
from qdrant_client.models import FieldCondition, Filter, MatchAny, MatchValue

from core.collections import COLLECTIONS, TAGS_PAYLOAD_FIELD
from core.indexer import PATH_DIRS_FIELD, TAGGED_COLLECTIONS
from server.api import validate_path
from server.config import settings
from server.principal import Principal, principal_of, require_admin_or_write_all

index_router = APIRouter()

_COLLECTIONS = {c.name for c in COLLECTIONS}
_MEMORY = "mnemos_memory"
_FILE_FIELDS = ("file_path", "tags", "language", "last_indexed_at", "file_mtime", "source_url")
_CHUNK_FIELDS = ("chunk_index", "chunk_type", "symbol_name", "section", "preamble", "content")


class CheckPathBody(BaseModel):
    path: str = Field(min_length=1, max_length=4096)


def _known(collection: str) -> None:
    if collection not in _COLLECTIONS:
        raise HTTPException(status_code=404, detail=f"Unknown collection: {collection}")


def _scope(principal: Principal, collection: str) -> list:
    """Conditions keeping a tagged collection to what `principal` may read."""
    scope = principal.read_filter()
    if collection not in TAGGED_COLLECTIONS or scope is None:
        return []
    return [FieldCondition(key=TAGS_PAYLOAD_FIELD, match=MatchAny(any=scope))]


@index_router.get("/api/index/browse")
def browse(
    request: Request,
    collection: str,
    tag: str | None = None,
    directory: str | None = None,
    cursor: str | None = None,
    limit: int = Query(50, ge=1, le=200),
):
    """One item per indexed file: each file's first chunk stands for it (a memory is one point)."""
    _known(collection)
    must: list = _scope(principal_of(request), collection)
    if collection != _MEMORY:
        must.append(FieldCondition(key="chunk_index", match=MatchValue(value=0)))
    if tag:
        must.append(FieldCondition(key=TAGS_PAYLOAD_FIELD, match=MatchAny(any=[tag])))
    if directory:
        must.append(FieldCondition(key=PATH_DIRS_FIELD, match=MatchValue(value=directory.rstrip("/") + "/")))
    query = Filter(must=must) if must else None
    qdrant = request.app.state.qdrant
    points, next_offset = qdrant.scroll(
        collection_name=collection, scroll_filter=query, limit=limit, offset=cursor or None,
        with_payload=list(_FILE_FIELDS), with_vectors=False,
    )
    return {
        "items": [{"collection": collection, **{k: (p.payload or {}).get(k) for k in _FILE_FIELDS}} for p in points],
        "next_cursor": str(next_offset) if next_offset is not None else None,
        "total": qdrant.count(collection_name=collection, count_filter=query, exact=True).count,
    }


@index_router.get("/api/index/file")
def file_detail(request: Request, collection: str, file_path: str):
    """Every chunk of one indexed file, in order, with what the caller may do with it."""
    _known(collection)
    principal = principal_of(request)
    query = Filter(must=[FieldCondition(key="file_path", match=MatchValue(value=file_path)),
                         *_scope(principal, collection)])
    chunks: list[dict[str, Any]] = []
    offset = None
    while True:
        points, offset = request.app.state.qdrant.scroll(
            collection_name=collection, scroll_filter=query, limit=256, offset=offset,
            with_payload=True, with_vectors=False,
        )
        chunks.extend(p.payload or {} for p in points)
        if offset is None:
            break
    if not chunks:
        raise HTTPException(status_code=404, detail=f"{file_path!r} is not indexed in {collection}")
    chunks.sort(key=lambda c: int(c.get("chunk_index") or 0))
    first = chunks[0]
    tags = sorted({t for c in chunks for t in (c.get(TAGS_PAYLOAD_FIELD) or [])})
    return {
        "collection": collection,
        **{k: first.get(k) for k in _FILE_FIELDS},
        "tags": first.get(TAGS_PAYLOAD_FIELD) or [],
        "reindexable": principal.write_all and _on_disk(file_path),
        "deletable": principal.write_all or principal.can_write_resolved(tags),
        "chunks": [{k: c.get(k) for k in _CHUNK_FIELDS} for c in chunks],
    }


def _on_disk(file_path: str) -> bool:
    """Under a server mount and present: a reindex can read it again (pushed content cannot)."""
    try:
        return validate_path(file_path).is_file()
    except HTTPException:
        return False


def _route(path: str) -> str | None:
    """The collection the watcher sends `path` to, as watcher.main.PathRouter decides."""
    code = settings.codebase_path.rstrip("/") + "/"
    config = settings.claude_config_path.rstrip("/") + "/"
    if path.startswith(code):
        return "mnemos_code"
    if path.startswith(config + "skills/"):
        return "mnemos_skills"
    if path.startswith(config + "docs/"):
        return "mnemos_docs"
    return None


@index_router.post("/api/index/check-path")
def check_path(body: CheckPathBody, request: Request):
    """Would this path be indexed, under which tags, and if not, which rule says no."""
    require_admin_or_write_all(principal_of(request))
    raw = body.path.strip()
    path = os.path.normpath(raw if raw.startswith("/") else os.path.join(settings.codebase_path, raw))
    collection = _route(path)
    explained = request.app.state.indexer.explain(path)
    chunks = 0
    if collection:
        chunks = request.app.state.qdrant.count(
            collection_name=collection,
            count_filter=Filter(must=[FieldCondition(key="file_path", match=MatchValue(value=path))]),
            exact=True,
        ).count
    return {"path": path, "collection": collection, "indexed": chunks > 0, "chunks": chunks, **explained}
