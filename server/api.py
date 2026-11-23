from __future__ import annotations

import json
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Literal, Optional

from fastapi import APIRouter, BackgroundTasks, HTTPException, Query, Request
from pydantic import BaseModel
from qdrant_client.models import (
    FieldCondition,
    Filter,
    FilterSelector,
    MatchAny,
    MatchValue,
    PointStruct,
)

from core.cache import CACHE_COLLECTION
from core.collections import COLLECTIONS, TAGS_PAYLOAD_FIELD
from core.indexer import CHUNK_FACET_FIELDS, TAGGED_COLLECTIONS, facet_fields
from core.llm import LLMError
from server.config import settings
from server.principal import (
    Principal,
    forbidden,
    principal_of,
    require_admin,
    require_write_all,
)
from server.trace import RetrievalTrace

# Handlers are plain `def` on purpose: every one of them calls blocking code
# (torch inference, the sync Qdrant client, LLM calls). FastAPI runs `def`
# handlers in its threadpool; an `async def` would run them on the event loop
# and serialize every request behind the slowest one.
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
    tags: Optional[List[str]] = None  # Override the auto-detected tag list for this file.
    source_url: Optional[str] = None  # Where the original lives (wiki page, ticket, web page).
    file_mtime: Optional[float] = None  # Source last-modified (epoch seconds); defaults to now.


class _Diagnosable(BaseModel):
    # Per-stage record of how the results were reached (Playground). Costs two
    # extra Qdrant queries per collection, so it is never on by default.
    trace: bool = False
    # Tags the query-log entry so the dashboard can leave test traffic out.
    # A closed set: free text here would let any client write into the log.
    source: Optional[Literal["playground"]] = None


class SearchRequest(_Diagnosable):
    query: str
    collections: Optional[List[str]] = None
    file_types: Optional[List[str]] = None
    path_filter: Optional[str] = None
    limit: int = 5
    tags_any: Optional[List[str]] = None  # OR filter on payload `tags`.
    tags_all: Optional[List[str]] = None  # AND filter on payload `tags`.
    mode: str = "preview"                 # "preview" truncates chunks; "full" returns whole chunks.
    # False skips the semantic cache both ways: no stale answer, nothing stored.
    use_cache: bool = True


class SearchCodeRequest(_Diagnosable):
    query: str
    language: Optional[str] = None
    symbol_type: Optional[str] = None
    path_filter: Optional[str] = None
    limit: int = 3
    tags_any: Optional[List[str]] = None
    tags_all: Optional[List[str]] = None
    mode: str = "preview"


class SearchSkillsRequest(_Diagnosable):
    query: str
    limit: int = 3


class ReindexRequest(BaseModel):
    collection: str
    path: Optional[str] = None
    full: bool = False
    recreate: bool = False           # Drop + recreate (needed when migrating to hybrid schema)
    workers: int = 1                 # Parallel worker threads for indexing
    tags: Optional[List[str]] = None # Override the auto-detected tag list for every file under `path`.
    exclude_exts: List[str] = []     # Extra extensions to skip for this run (on top of the built-in deny list).
    exclude_dirs: List[str] = []     # Extra directory names to skip for this run.


class MemoryCreateRequest(BaseModel):
    content: str
    project: Optional[str] = None
    memory_type: str = "general"
    tags: List[str] = []
    status: str = "approved"


class MemoryReviewRequest(BaseModel):
    action: str  # "approve" or "reject"


class MemoryExtractRequest(BaseModel):
    commit_message: str
    diff: str
    author: Optional[str] = None


# ---------------------------------------------------------------------------
# Internal endpoints (used by watcher)
# ---------------------------------------------------------------------------

_ALLOWED_BASE_DIRS = [
    settings.codebase_path,
    settings.claude_config_path,
]


def invalidate_cache(cache) -> None:
    """The index changed: no cached answer may outlive it, on any pod (the cache lives in Qdrant)."""
    if cache is not None and cache.enabled:
        cache.invalidate()


def _index_changed(request: Request) -> None:
    invalidate_cache(getattr(request.app.state, "cache", None))


# Where a reindex walks when the caller names no path. Connector and memory
# collections are pushed through the API: they have nothing to walk.
def default_reindex_root(collection: str) -> str | None:
    return {
        "mnemos_code": settings.codebase_path,
        "mnemos_skills": f"{settings.claude_config_path}/skills",
        "mnemos_docs": f"{settings.claude_config_path}/docs",
    }.get(collection)


def validate_path(file_path: str) -> Path:
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
def internal_reindex(body: ReindexInternalRequest, request: Request):
    require_write_all(principal_of(request))
    safe_path = validate_path(body.file_path)

    if body.event == "deleted":
        request.app.state.indexer.delete_file(
            file_path=body.file_path,
            collection=body.collection,
        )
        _index_changed(request)
        return {"status": "deleted", "file_path": body.file_path}

    if not safe_path.exists():
        raise HTTPException(status_code=404, detail=f"File not found: {body.file_path}")

    try:
        content = safe_path.read_text(encoding="utf-8")
    except (UnicodeDecodeError, OSError):
        return {"status": "skipped", "file_path": body.file_path, "reason": "binary or unreadable"}

    request.app.state.indexer.ensure_collection(body.collection)
    count = request.app.state.indexer.index_file(
        content=content,
        file_path=body.file_path,
        collection=body.collection,
        file_mtime=safe_path.stat().st_mtime,
    )
    _index_changed(request)
    return {"status": "indexed", "file_path": body.file_path, "chunks": count}


# ---------------------------------------------------------------------------
# Push API (CI, git hooks, connectors)
# ---------------------------------------------------------------------------


def _read_conditions(principal: Principal) -> list[FieldCondition]:
    """The `must` conditions keeping a tagged collection to what `principal` may read."""
    scope = principal.read_filter()
    if scope is None:
        return []
    return [FieldCondition(key=TAGS_PAYLOAD_FIELD, match=MatchAny(any=scope))]


def _require_push(principal: Principal, indexer, body: IndexPushRequest) -> None:
    if principal.write_all:
        return
    if body.collection not in TAGGED_COLLECTIONS:
        require_write_all(principal)
    # An empty `tags` list is no override: the indexer resolves the tags itself.
    if body.tags:
        if not principal.can_write_declared(body.tags):
            raise forbidden(
                f"Tenant {principal.name!r} cannot write tags {sorted(set(body.tags))}"
            )
        return
    resolved = indexer.resolve_tags(body.file_path, None)
    if not principal.can_write_resolved(resolved):
        raise forbidden(
            f"Tenant {principal.name!r} cannot write {body.file_path!r} (tags {resolved})"
        )


def _require_delete(principal: Principal, qdrant, collection: str, file_path: str) -> None:
    """Every stored chunk of the file must be writable; a file never indexed passes."""
    if principal.write_all:
        return
    if collection not in TAGGED_COLLECTIONS:
        require_write_all(principal)
    chunks = _scroll_all(
        qdrant,
        collection,
        Filter(must=[FieldCondition(key="file_path", match=MatchValue(value=file_path))]),
        with_payload=[TAGS_PAYLOAD_FIELD],
    )
    for chunk in chunks:
        tags = (chunk.payload or {}).get(TAGS_PAYLOAD_FIELD) or []
        if not principal.can_write_resolved(tags):
            raise forbidden(f"Tenant {principal.name!r} cannot delete {file_path!r} (tags {tags})")


@api_router.post("/api/index")
def push_index(body: IndexPushRequest, request: Request):
    if body.collection not in _VALID_COLLECTIONS:
        raise HTTPException(status_code=400, detail=f"Unknown collection: {body.collection}")
    indexer = request.app.state.indexer
    _require_push(principal_of(request), indexer, body)
    indexer.ensure_collection(body.collection)
    count = indexer.index_file(
        content=body.content,
        file_path=body.file_path,
        collection=body.collection,
        tags=body.tags,
        source_url=body.source_url,
        file_mtime=body.file_mtime,
    )
    _index_changed(request)
    return {"status": "indexed", "file_path": body.file_path, "chunks": count}


@api_router.delete("/api/index/{collection}/{file_path:path}")
def delete_index(collection: str, file_path: str, request: Request):
    _require_delete(principal_of(request), request.app.state.qdrant, collection, file_path)
    request.app.state.indexer.delete_file(
        file_path=file_path,
        collection=collection,
    )
    _index_changed(request)
    return {"status": "deleted", "collection": collection, "file_path": file_path}


@api_router.get("/api/index/{collection}/files")
def list_indexed_files(collection: str, tag: str, request: Request):
    """Files indexed under one tag, with their `file_mtime` — the sync state of a connector."""
    if collection not in _VALID_COLLECTIONS:
        raise HTTPException(status_code=400, detail=f"Unknown collection: {collection}")
    principal = principal_of(request)
    if not principal.can_read([tag]):
        raise forbidden(f"Tenant {principal.name!r} cannot read tag {tag!r}")
    files = request.app.state.indexer.list_files(collection, tag=tag)
    return {"collection": collection, "tag": tag, "files": files}


# Payload fields the dashboard may count. Qdrant facets need a keyword
# index; `core.indexer.facet_fields` says which collection carries which.
_FACET_FIELDS = (TAGS_PAYLOAD_FIELD, *CHUNK_FACET_FIELDS)


@api_router.get("/api/index/facets")
def index_facets(
    request: Request,
    collection: str,
    field: str,
    limit: int = Query(20, ge=1, le=1000),
):
    """Most frequent values of one payload field, with their point counts."""
    if collection not in _VALID_COLLECTIONS:
        raise HTTPException(status_code=404, detail=f"Unknown collection: {collection}")
    if field not in _FACET_FIELDS:
        raise HTTPException(
            status_code=400,
            detail=f"Field {field!r} cannot be faceted; allowed: {', '.join(_FACET_FIELDS)}",
        )
    if field not in facet_fields(collection):
        raise HTTPException(
            status_code=400, detail=f"Field {field!r} is not indexed on {collection}"
        )
    conditions = (
        _read_conditions(principal_of(request)) if collection in TAGGED_COLLECTIONS else []
    )
    response = request.app.state.qdrant.facet(
        collection_name=collection,
        key=field,
        limit=limit,
        facet_filter=Filter(must=conditions) if conditions else None,
    )
    return {
        "collection": collection,
        "field": field,
        "counts": [{"value": str(hit.value), "count": hit.count} for hit in response.hits],
    }


# ---------------------------------------------------------------------------
# Search API (used by CLI)
# ---------------------------------------------------------------------------


def _respond(results: list, trace: RetrievalTrace | None, rows: list[dict] | None = None) -> dict:
    """`{"results": …}`, plus `"trace"` when one was requested."""
    body: dict = {"results": rows if rows is not None else [r.model_dump() for r in results]}
    if trace is not None:
        body["trace"] = trace.finish(results)
    return body


@api_router.post("/api/search")
def search(body: SearchRequest, request: Request):
    trace = RetrievalTrace() if body.trace else None
    results = request.app.state.search_service.search(
        query=body.query,
        collections=body.collections,
        file_types=body.file_types,
        path_filter=body.path_filter,
        limit=body.limit,
        tags_any=body.tags_any,
        tags_all=body.tags_all,
        mode=body.mode,
        use_cache=body.use_cache,
        trace=trace,
        source=body.source,
        read_scope=principal_of(request).read_filter(),
    )
    return _respond(results, trace)


@api_router.post("/api/search-code")
def search_code(body: SearchCodeRequest, request: Request):
    trace = RetrievalTrace() if body.trace else None
    results = request.app.state.search_service.search_code(
        query=body.query,
        language=body.language,
        symbol_type=body.symbol_type,
        path_filter=body.path_filter,
        limit=body.limit,
        tags_any=body.tags_any,
        tags_all=body.tags_all,
        mode=body.mode,
        trace=trace,
        source=body.source,
        read_scope=principal_of(request).read_filter(),
    )
    return _respond(results, trace)


@api_router.post("/api/search-skills")
def search_skills(body: SearchSkillsRequest, request: Request):
    trace = RetrievalTrace() if body.trace else None
    results = request.app.state.search_service.search_skills(
        query=body.query,
        limit=body.limit,
        trace=trace,
        source=body.source,
    )
    return _respond(results, trace)


class SearchMemoryRequest(_Diagnosable):
    query: str
    memory_type: Optional[str] = None
    limit: int = 5
    tags_any: Optional[List[str]] = None
    tags_all: Optional[List[str]] = None
    mode: str = "preview"


@api_router.post("/api/search-memory")
def search_memory(body: SearchMemoryRequest, request: Request):
    trace = RetrievalTrace() if body.trace else None
    results = request.app.state.search_service.search_memory(
        query=body.query,
        memory_type=body.memory_type,
        limit=body.limit,
        tags_any=body.tags_any,
        tags_all=body.tags_all,
        mode=body.mode,
        trace=trace,
        source=body.source,
        read_scope=principal_of(request).read_filter(),
    )
    # Map memory results to a search-result-compatible shape so eval/harness can consume them.
    rows = [
        {
            "file_path": f"memory://{r.id}",
            "content": r.content,
            "score": r.score,
            "collection": "mnemos_memory",
            "memory_type": r.memory_type,
            "tags": r.tags,
        }
        for r in results
    ]
    return _respond(results, trace, rows)


class EvalSampleRequest(BaseModel):
    collection: str
    count: int = 10
    seed: Optional[int] = None


# File paths that should never be sampled into a golden set even if they
# slipped past the indexing skip rules. Patterns are matched as substrings on
# the full file_path.
_GOLDEN_PATH_BLOCKLIST: tuple[str, ...] = (
    ".bak",
    "/_nuxt/",
    "/.terraform/",
    "/webapp/android/",
    "/webapp/ios/",
    "/app/src/main/assets/",
    "/App/App/public/",
    "/backup/",
    "/dist/",
    "/build/",
    "/.next/",
    "/.output/",
    "/node_modules/",
    "/vendor/",
    "/Pods/",
    "/coverage/",
    "/test-results/",
)


def _is_blocklisted_for_golden(file_path: str) -> bool:
    return any(pat in file_path for pat in _GOLDEN_PATH_BLOCKLIST)


@api_router.post("/api/eval/sample")
def eval_sample(body: EvalSampleRequest, request: Request):
    """Return up to `count` random chunks from a collection (for golden-set generation).

    Filters out chunks whose file_path looks like generated / build / backup
    content — those make for unfair eval ground truth because no retrieval
    upgrade can rescue a question that points to a junk file.
    """
    require_admin(principal_of(request))
    if body.collection not in _VALID_COLLECTIONS:
        raise HTTPException(status_code=400, detail=f"Unknown collection: {body.collection}")

    import random

    qdrant = request.app.state.qdrant
    # Scroll wider than `count` so we still have enough after filtering junk paths.
    scroll_limit = max(body.count * 30, 500)
    points, _ = qdrant.scroll(
        collection_name=body.collection,
        limit=scroll_limit,
        with_payload=True,
        with_vectors=False,
    )
    if not points:
        return {"chunks": []}

    clean = [p for p in points if not _is_blocklisted_for_golden(
        (p.payload or {}).get("file_path", "")
    )]
    if not clean:
        return {"chunks": [], "filtered_out": len(points)}

    rng = random.Random(body.seed)
    sampled = rng.sample(clean, min(body.count, len(clean)))
    chunks = []
    for p in sampled:
        payload = p.payload or {}
        chunks.append(
            {
                "content": payload.get("content", ""),
                "file_path": payload.get("file_path", ""),
                "chunk_type": payload.get("chunk_type", ""),
                "language": payload.get("language", ""),
                "symbol_name": payload.get("symbol_name", ""),
                "collection": body.collection,
            }
        )
    return {"chunks": chunks, "filtered_out": len(points) - len(clean)}


# ---------------------------------------------------------------------------
# Reindex API (used by CLI)
# ---------------------------------------------------------------------------


def _index_one_file(indexer, collection: str, fp, tags: list[str] | None = None) -> int:
    """Index a single file. Returns the number of chunks indexed (0 on error)."""
    try:
        content = fp.read_text(encoding="utf-8")
        return indexer.index_file(
            content=content,
            file_path=str(fp),
            collection=collection,
            file_mtime=fp.stat().st_mtime,
            tags=tags,
        )
    except Exception:
        return -1  # sentinel for "skipped on error"


def run_reindex(
    indexer,
    collection: str,
    base_path,
    full: bool,
    workers: int = 1,
    tags: list[str] | None = None,
    exclude_exts: list[str] | None = None,
    exclude_dirs: list[str] | None = None,
    cache=None,
) -> None:
    """Background task: walk files and index them, optionally in parallel."""
    import logging
    from concurrent.futures import ThreadPoolExecutor, as_completed

    logger = logging.getLogger("rag.reindex")

    exclude_exts = exclude_exts or []
    exclude_dirs = exclude_dirs or []

    # Ask the indexer rather than calling _should_skip directly: it is the one
    # place that unions the built-in policy, the per-prefix rules from
    # config/projects.yaml and these per-run extras. Re-deriving that here is
    # exactly the drift core.path_filter exists to prevent.
    files = [fp for fp in (base_path.rglob("*") if full else [base_path])
             if fp.is_file()
             and not indexer.should_skip(str(fp), exclude_exts, exclude_dirs)]

    if not files:
        logger.info(f"Reindex: collection={collection} no files to index")
        return

    indexed = 0
    skipped = 0
    workers = max(1, int(workers))

    if workers == 1:
        for fp in files:
            n = _index_one_file(indexer, collection, fp, tags=tags)
            if n < 0:
                skipped += 1
            else:
                indexed += n
    else:
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = [pool.submit(_index_one_file, indexer, collection, fp, tags) for fp in files]
            for fut in as_completed(futures):
                n = fut.result()
                if n < 0:
                    skipped += 1
                else:
                    indexed += n

    logger.info(
        f"Reindex complete: collection={collection} files={len(files)} "
        f"indexed_chunks={indexed} skipped_files={skipped} workers={workers}"
    )
    # Again at the end: answers cached while the walk ran predate part of it.
    invalidate_cache(cache)


@api_router.post("/api/reindex")
def reindex(body: ReindexRequest, request: Request, background_tasks: BackgroundTasks):
    require_write_all(principal_of(request))
    indexer = request.app.state.indexer

    if body.recreate:
        indexer.recreate_collection_hybrid(body.collection)
    else:
        indexer.ensure_collection(body.collection)

    cache = getattr(request.app.state, "cache", None)
    invalidate_cache(cache)

    if body.path:
        base_path = validate_path(body.path)
        background_tasks.add_task(
            run_reindex,
            indexer,
            body.collection,
            base_path,
            body.full,
            body.workers,
            body.tags,
            body.exclude_exts,
            body.exclude_dirs,
            cache,
        )
        return {
            "status": "reindex_started",
            "collection": body.collection,
            "path": str(base_path),
            "workers": body.workers,
            "recreated": body.recreate,
            "tags": body.tags,
            "exclude_exts": body.exclude_exts,
            "exclude_dirs": body.exclude_dirs,
        }

    return {"status": "no_path", "collection": body.collection}


# ---------------------------------------------------------------------------
# Memory API (used by CLI)
# ---------------------------------------------------------------------------

_MEMORY_COLLECTION = "mnemos_memory"


# Qdrant scroll page; endpoints loop over pages instead of trusting one call.
_SCROLL_PAGE = 500


def _scroll_all(
    qdrant,
    collection: str,
    scroll_filter: Filter | None = None,
    with_payload: bool | list[str] = True,
):
    """Every point of `collection`, page by page, no vectors."""
    offset = None
    while True:
        records, offset = qdrant.scroll(
            collection_name=collection,
            scroll_filter=scroll_filter,
            limit=_SCROLL_PAGE,
            offset=offset,
            with_payload=with_payload,
            with_vectors=False,
        )
        yield from records
        if offset is None or not records:
            return


def _created_sort_key(payload: dict) -> datetime:
    """Newest-first key; entries without a usable `created_at` sort last."""
    try:
        moment = datetime.fromisoformat(payload.get("created_at") or "")
    except (TypeError, ValueError):
        return datetime.min.replace(tzinfo=timezone.utc)
    return moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)


@api_router.get("/api/memory")
def list_memory(
    request: Request,
    status: Optional[str] = None,
    memory_type: Optional[str] = Query(None, alias="type"),
    q: Optional[str] = None,
    offset: int = Query(0, ge=0),
    limit: int = Query(1000, ge=1, le=10000),
):
    """One page of memories, newest first, with the total that matches.

    Filtered and sorted over the whole collection, then sliced: a page is a
    window on every match, not on whatever Qdrant happened to return first.
    The memory collection is small (thousands), so reading its payloads is
    milliseconds.
    """
    must = _read_conditions(principal_of(request))
    if status:
        must.append(FieldCondition(key="status", match=MatchValue(value=status)))
    if memory_type:
        must.append(FieldCondition(key="memory_type", match=MatchValue(value=memory_type)))
    payloads = [
        r.payload
        for r in _scroll_all(
            request.app.state.qdrant, _MEMORY_COLLECTION, Filter(must=must) if must else None
        )
    ]
    needle = (q or "").strip().lower()
    if needle:
        payloads = [
            p for p in payloads
            if needle in (p.get("content") or "").lower()
            or any(needle in str(t).lower() for t in p.get("tags") or [])
        ]
    payloads.sort(key=_created_sort_key, reverse=True)
    return {
        "entries": payloads[offset:offset + limit],
        "total": len(payloads),
        "offset": offset,
        "limit": limit,
    }


def _utc_day(created_at) -> str | None:
    """`YYYY-MM-DD` (UTC) of an ISO timestamp; None when unparseable."""
    if not isinstance(created_at, str):
        return None
    try:
        moment = datetime.fromisoformat(created_at)
    except ValueError:
        return None
    if moment.tzinfo is not None:
        moment = moment.astimezone(timezone.utc)
    return moment.date().isoformat()


@api_router.get("/api/memory/stats")
def memory_stats(request: Request):
    """Counts over every readable memory, whatever its status. Payload fields only."""
    readable = _read_conditions(principal_of(request))
    by_status: dict[str, int] = {}
    by_type: dict[str, int] = {}
    by_day: dict[str, int] = {}
    total = 0
    for record in _scroll_all(
        request.app.state.qdrant,
        _MEMORY_COLLECTION,
        Filter(must=readable) if readable else None,
        with_payload=["status", "memory_type", "created_at"],
    ):
        payload = record.payload or {}
        total += 1
        status = payload.get("status") or "unknown"
        by_status[status] = by_status.get(status, 0) + 1
        memory_type = payload.get("memory_type") or "unknown"
        by_type[memory_type] = by_type.get(memory_type, 0) + 1
        day = _utc_day(payload.get("created_at"))
        if day:
            by_day[day] = by_day.get(day, 0) + 1
    return {
        "total": total,
        "by_status": by_status,
        "by_type": by_type,
        "by_day": dict(sorted(by_day.items())),
    }


@api_router.post("/api/memory")
def create_memory(body: MemoryCreateRequest, request: Request):
    deduplicator = request.app.state.deduplicator
    principal = principal_of(request)
    if not principal.can_write_declared(body.tags):
        raise forbidden(f"Tenant {principal.name!r} cannot write tags {sorted(set(body.tags))}")

    from core.models import ExtractedMemory
    memory = ExtractedMemory(
        content=body.content,
        memory_type=body.memory_type,
        project=body.project,
        tags=body.tags,
    )

    result = deduplicator.deduplicate_and_store(memory, status=body.status)
    _index_changed(request)
    return {"status": "created", "id": result.memory_id, "action": result.action}


@api_router.post("/api/memory/{mem_id}/review")
def review_memory(mem_id: str, body: MemoryReviewRequest, request: Request):
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
    principal = principal_of(request)
    stored_tags = record.payload.get(TAGS_PAYLOAD_FIELD) or []
    if not principal.can_write_resolved(stored_tags):
        raise forbidden(f"Tenant {principal.name!r} cannot review memory {mem_id!r} (tags {stored_tags})")
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
    _index_changed(request)
    return {"status": new_status, "id": mem_id}


@api_router.post("/api/memory/extract")
def extract_memories(body: MemoryExtractRequest, request: Request):
    # The LLM picks the tags, so only a tenant allowed to write any tag may run it.
    require_write_all(principal_of(request))
    extractor = request.app.state.memory_extractor
    deduplicator = request.app.state.deduplicator

    try:
        extracted = extractor.extract(
            commit_message=body.commit_message,
            diff=body.diff,
        )
    except LLMError as exc:
        # 502 rather than a 200 with `extracted: 0`: the git hook fires this
        # and discards the response, so a silent success means nobody ever
        # learns the LLM is unreachable.
        raise HTTPException(
            status_code=502,
            detail=f"memory extraction unavailable: {exc}",
        ) from exc

    results = []
    for memory in extracted:
        dedup_result = deduplicator.deduplicate_and_store(memory)
        results.append({
            "id": dedup_result.memory_id,
            "content": memory.content,
            "action": dedup_result.action,
            "status": dedup_result.status,
        })

    if results:
        _index_changed(request)
    return {"extracted": len(results), "memories": results}


# ---------------------------------------------------------------------------
# Status API
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Observability API (used by the dashboard)
# ---------------------------------------------------------------------------


def _tail_jsonl(path: Path, limit: int) -> list[dict]:
    """Last `limit` well-formed JSON objects of a JSONL file, oldest first.

    Read backwards in blocks: the query log only grows, and a dashboard that
    parses the whole file gets slower every day it works.
    """
    if not path.exists():
        return []

    block = 64 * 1024
    lines: list[bytes] = []
    with path.open("rb") as fh:
        fh.seek(0, 2)
        pos = fh.tell()
        buffer = b""
        while pos > 0 and len(lines) <= limit:
            step = min(block, pos)
            pos -= step
            fh.seek(pos)
            buffer = fh.read(step) + buffer
            lines = buffer.split(b"\n")
        candidates = [ln for ln in lines if ln.strip()]

    out: list[dict] = []
    for raw in candidates[-limit:]:
        try:
            out.append(json.loads(raw))
        except ValueError:
            # A torn last line is normal while the server is appending.
            continue
    return out


@api_router.get("/api/query-log")
def query_log(request: Request, limit: int = 500, intent: Optional[str] = None):
    """Recent retrieval calls, newest last.

    `enabled` is reported separately from an empty list on purpose: a
    dashboard showing nothing must be able to say whether that means "no
    traffic" or "nothing is being recorded". Conflating the two is how a
    logger that had been off for a week went unnoticed.
    """
    require_admin(principal_of(request))
    # The running logger, not the setting: it can be toggled live from the
    # config page, and it stays off when its directory cannot be created.
    query_logger = getattr(request.app.state, "query_logger", None)
    enabled = query_logger.enabled if query_logger is not None else settings.mnemos_query_log_enabled
    entries = _tail_jsonl(Path(settings.mnemos_query_log_path), max(1, min(limit, 5000)))
    if intent:
        entries = [e for e in entries if e.get("intent") == intent]
    return {"enabled": enabled, "count": len(entries), "entries": entries}


@api_router.get("/api/eval/runs")
def eval_runs(request: Request, tag: Optional[str] = None):
    """Eval runs on disk: summaries by default, one full run when `tag` is set.

    Per-question `results` carry indexed file paths, so they are only returned
    for an explicitly requested tag rather than in the listing.
    """
    require_admin(principal_of(request))
    runs_dir = Path(settings.mnemos_eval_runs_path)
    if not runs_dir.is_dir():
        # No runs yet is a normal state for a listing, but asking for a
        # specific tag that cannot exist is still a miss.
        if tag:
            raise HTTPException(status_code=404, detail=f"No eval run tagged {tag!r}")
        return {"runs": []}

    if tag:
        for path in sorted(runs_dir.glob("*.json")):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if data.get("tag") == tag:
                return data
        raise HTTPException(status_code=404, detail=f"No eval run tagged {tag!r}")

    runs = []
    for path in sorted(runs_dir.glob("*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            # A half-written or hand-edited run must not break the listing.
            continue
        runs.append({
            "tag": data.get("tag") or path.stem,
            "created_at": data.get("created_at"),
            "report": data.get("report", {}),
        })
    runs.sort(key=lambda r: r.get("created_at") or "", reverse=True)
    return {"runs": runs}


@api_router.get("/api/status")
def api_status(request: Request):
    qdrant = request.app.state.qdrant
    # Logical names, resolved through aliases: after an embedding migration the
    # physical collections carry a model suffix that clients must not see.
    index_info = {}
    for name in [c.name for c in COLLECTIONS] + [CACHE_COLLECTION]:
        if not qdrant.collection_exists(name):
            continue
        try:
            info = qdrant.get_collection(name)
            index_info[name] = {
                "vectors_count": info.indexed_vectors_count,
                "points_count": info.points_count,
                "status": str(info.status),
            }
        except Exception:
            index_info[name] = {"error": "unavailable"}

    return {"status": "healthy", "collections": index_info}


# The LLM probe crosses the network (and may hit a paid API's rate limit):
# a dashboard polling every few seconds gets the cached answer.
_LLM_PROBE_TTL_S = 30.0
_LLM_PROBE_TIMEOUT_S = 3.0
_llm_probe_lock = threading.Lock()


def _redact_key(text: str) -> str:
    key = settings.mnemos_llm_api_key
    return text.replace(key, "***") if key else text


def _probe_qdrant(qdrant) -> dict:
    started = time.perf_counter()
    error = None
    try:
        qdrant.get_collections()
    except Exception as exc:
        error = f"{type(exc).__name__}: {exc}"
    return {
        "ok": error is None,
        "latency_ms": round((time.perf_counter() - started) * 1000, 1),
        "error": error,
    }


def _probe_llm(state) -> dict:
    llm = getattr(state, "llm", None)
    result = {
        "ok": False,
        "provider": getattr(llm, "name", settings.mnemos_llm_provider),
        "model": getattr(llm, "model", settings.mnemos_llm_model),
        "latency_ms": None,
        "error": None,
    }
    if llm is None:
        result["error"] = "LLM provider not initialised"
    else:
        started = time.perf_counter()
        try:
            llm.ping(timeout=_LLM_PROBE_TIMEOUT_S)
            result["ok"] = True
        except LLMError as exc:
            result["error"] = _redact_key(str(exc))
        except Exception as exc:
            # Unexpected: the class only, the message could quote a request.
            result["error"] = f"unexpected {type(exc).__name__}"
        result["latency_ms"] = round((time.perf_counter() - started) * 1000, 1)
    result["checked_at"] = datetime.now(timezone.utc).isoformat()
    return result


@api_router.get("/api/health/deps")
def health_deps(request: Request):
    """Reachability of Qdrant (live) and of the LLM provider (cached 30 s)."""
    state = request.app.state
    with _llm_probe_lock:
        cached = getattr(state, "llm_probe", None)
        if cached is None or time.monotonic() - cached[0] > _LLM_PROBE_TTL_S:
            cached = (time.monotonic(), _probe_llm(state))
            state.llm_probe = cached
    return {"qdrant": _probe_qdrant(state.qdrant), "llm": cached[1]}
