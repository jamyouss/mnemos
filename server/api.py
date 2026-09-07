from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional

from fastapi import APIRouter, BackgroundTasks, HTTPException, Request
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

from core.collections import COLLECTIONS
from core.llm import LLMError
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
    tags: Optional[List[str]] = None  # Override the auto-detected tag list for this file.


class SearchRequest(BaseModel):
    query: str
    collections: Optional[List[str]] = None
    file_types: Optional[List[str]] = None
    path_filter: Optional[str] = None
    limit: int = 5
    tags_any: Optional[List[str]] = None  # OR filter on payload `tags`.
    tags_all: Optional[List[str]] = None  # AND filter on payload `tags`.
    mode: str = "preview"                 # "preview" truncates chunks; "full" returns whole chunks.


class SearchCodeRequest(BaseModel):
    query: str
    language: Optional[str] = None
    symbol_type: Optional[str] = None
    path_filter: Optional[str] = None
    limit: int = 3
    tags_any: Optional[List[str]] = None
    tags_all: Optional[List[str]] = None
    mode: str = "preview"


class SearchSkillsRequest(BaseModel):
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
        tags=body.tags,
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
        tags_any=body.tags_any,
        tags_all=body.tags_all,
        mode=body.mode,
    )
    return {"results": [r.model_dump() for r in results]}


@api_router.post("/api/search-code")
async def search_code(body: SearchCodeRequest, request: Request):
    results = request.app.state.search_service.search_code(
        query=body.query,
        language=body.language,
        symbol_type=body.symbol_type,
        path_filter=body.path_filter,
        limit=body.limit,
        tags_any=body.tags_any,
        tags_all=body.tags_all,
        mode=body.mode,
    )
    return {"results": [r.model_dump() for r in results]}


@api_router.post("/api/search-skills")
async def search_skills(body: SearchSkillsRequest, request: Request):
    results = request.app.state.search_service.search_skills(
        query=body.query,
        limit=body.limit,
    )
    return {"results": [r.model_dump() for r in results]}


class SearchMemoryRequest(BaseModel):
    query: str
    memory_type: Optional[str] = None
    limit: int = 5
    tags_any: Optional[List[str]] = None
    tags_all: Optional[List[str]] = None
    mode: str = "preview"


@api_router.post("/api/search-memory")
async def search_memory(body: SearchMemoryRequest, request: Request):
    results = request.app.state.search_service.search_memory(
        query=body.query,
        memory_type=body.memory_type,
        limit=body.limit,
        tags_any=body.tags_any,
        tags_all=body.tags_all,
        mode=body.mode,
    )
    # Map memory results to a search-result-compatible shape so eval/harness can consume them.
    return {
        "results": [
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
    }


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
async def eval_sample(body: EvalSampleRequest, request: Request):
    """Return up to `count` random chunks from a collection (for golden-set generation).

    Filters out chunks whose file_path looks like generated / build / backup
    content — those make for unfair eval ground truth because no retrieval
    upgrade can rescue a question that points to a junk file.
    """
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


def _run_reindex(
    indexer,
    collection: str,
    base_path,
    full: bool,
    workers: int = 1,
    tags: list[str] | None = None,
    exclude_exts: list[str] | None = None,
    exclude_dirs: list[str] | None = None,
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


@api_router.post("/api/reindex")
async def reindex(body: ReindexRequest, request: Request, background_tasks: BackgroundTasks):
    indexer = request.app.state.indexer

    if body.recreate:
        indexer.recreate_collection_hybrid(body.collection)
    else:
        indexer.ensure_collection(body.collection)

    # Semantic cache (if enabled) is invalidated on any reindex so stale results
    # can never be served after the underlying index has changed.
    cache = getattr(request.app.state, "cache", None)
    if cache is not None and cache.enabled:
        cache.invalidate()

    if body.path:
        base_path = _validate_path(body.path)
        background_tasks.add_task(
            _run_reindex,
            indexer,
            body.collection,
            base_path,
            body.full,
            body.workers,
            body.tags,
            body.exclude_exts,
            body.exclude_dirs,
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
    deduplicator = request.app.state.deduplicator

    from core.models import ExtractedMemory
    memory = ExtractedMemory(
        content=body.content,
        memory_type=body.memory_type,
        project=body.project,
        tags=body.tags,
    )

    result = deduplicator.deduplicate_and_store(memory, status=body.status)
    return {"status": "created", "id": result.memory_id, "action": result.action}


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


@api_router.post("/api/memory/extract")
async def extract_memories(body: MemoryExtractRequest, request: Request):
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
async def query_log(limit: int = 500, intent: Optional[str] = None):
    """Recent retrieval calls, newest last.

    `enabled` is reported separately from an empty list on purpose: a
    dashboard showing nothing must be able to say whether that means "no
    traffic" or "nothing is being recorded". Conflating the two is how a
    logger that had been off for a week went unnoticed.
    """
    enabled = settings.mnemos_query_log_enabled
    entries = _tail_jsonl(Path(settings.mnemos_query_log_path), max(1, min(limit, 5000)))
    if intent:
        entries = [e for e in entries if e.get("intent") == intent]
    return {"enabled": enabled, "count": len(entries), "entries": entries}


@api_router.get("/api/eval/runs")
async def eval_runs(tag: Optional[str] = None):
    """Eval runs on disk: summaries by default, one full run when `tag` is set.

    Per-question `results` carry indexed file paths, so they are only returned
    for an explicitly requested tag rather than in the listing.
    """
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
async def api_status(request: Request):
    qdrant = request.app.state.qdrant
    collections = qdrant.get_collections().collections
    index_info = {}
    for coll in collections:
        try:
            info = qdrant.get_collection(coll.name)
            index_info[coll.name] = {
                "vectors_count": info.indexed_vectors_count,
                "points_count": info.points_count,
                "status": str(info.status),
            }
        except Exception:
            index_info[coll.name] = {"error": "unavailable"}

    return {"status": "healthy", "collections": index_info}
