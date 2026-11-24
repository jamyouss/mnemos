"""MCP tool definitions for the RAG server."""
from __future__ import annotations

import functools
import json
import threading
from typing import Any

import anyio

import mcp.types as types
from mcp.server import Server
from qdrant_client import QdrantClient

from core.collections import COLLECTIONS
from core.embeddings import EmbeddingService
from core.indexer import Indexer
from fastapi import HTTPException

from server.api import default_reindex_root, invalidate_cache, run_reindex, validate_path
from server.audit import record as audit_record
from server.indexing import ReindexJob
from server.principal import Principal, forbidden, principal_from_scope, require_write_all
from server.search import SearchService

# Hard cap on the JSON payload size for search-tool responses, in characters.
# 32K chars ≈ 8K tokens at the standard ~4-chars-per-token estimate. Even after
# preview-mode truncation and metadata whitelisting, a degenerate hit (a
# 1500-char preview × limit) can still chip away at the LLM context. This is
# the last-line defence so a Mnemos call can never blow the caller out of its
# context window. Overrideable via env (MNEMOS_RESPONSE_BUDGET_CHARS).
import os as _os
_RESPONSE_BUDGET_CHARS = int(_os.environ.get("MNEMOS_RESPONSE_BUDGET_CHARS", "32000"))


def _apply_response_budget(results: list[dict], budget_chars: int = _RESPONSE_BUDGET_CHARS) -> dict:
    """Wrap a list of result dicts in an envelope and drop the tail if needed.

    The envelope always has the same shape so the LLM caller can rely on it::

        {"results": [...], "kept": <int>, "dropped": <int>}

    The first result is always kept (even if it alone exceeds the budget) —
    returning zero hits on a successful retrieval would mislead the caller
    more than returning one big one.
    """
    envelope: dict = {"results": [], "kept": 0, "dropped": 0}
    if not results:
        return envelope

    # Cost of the empty envelope (give or take a few bytes — we only need a
    # ballpark since the per-result lengths dominate).
    base_cost = len(json.dumps(envelope, separators=(",", ":"), ensure_ascii=False))
    running = base_cost
    kept: list[dict] = []
    for r in results:
        item_len = len(json.dumps(r, separators=(",", ":"), ensure_ascii=False))
        # +1 for the comma between array elements (when there's already one).
        delta = item_len + (1 if kept else 0)
        if kept and running + delta > budget_chars:
            break
        kept.append(r)
        running += delta

    envelope["results"] = kept
    envelope["kept"] = len(kept)
    envelope["dropped"] = len(results) - len(kept)
    return envelope


# Registry of all tool definitions, used in list_tools and for testing
TOOL_DEFINITIONS: list[types.Tool] = [
    types.Tool(
        name="mnemos_search",
        description=(
            "Broad semantic search across docs + skills + code + external knowledge "
            "(wiki pages, tickets, web sites). Use when the question "
            "spans multiple sources or you're not sure where the answer lives. "
            "When you DO know where the answer lives, say so rather than searching "
            "everything: pass `collections` (mnemos_code, mnemos_docs, "
            "mnemos_skills, mnemos_memory, mnemos_knowledge), or call the dedicated tool — "
            "mnemos_search_code for implementations, mnemos_search_skills for "
            "agent skills, mnemos_search_memory for past decisions. Nothing infers "
            "the right source for you; you are the one who knows the intent. "
            "mnemos_knowledge hits carry metadata.source_url and tags such as "
            "`confluence:<SPACE>`, `jira:<PROJECT>` or `web:<host>`. "
            "Workflow: start with the default mode=preview and limit=5; if the "
            "right hit appears, re-call with mode=full and a path_filter to fetch "
            "the whole chunk. Use tags_any/tags_all to scope to a project. "
            "Truncated previews are marked metadata.truncated=true."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "The search query"},
                "collections": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": (
                        "Collections to search: mnemos_code, mnemos_docs, "
                        "mnemos_skills, mnemos_memory, mnemos_knowledge. Omit to search "
                        "everything but memory. Narrow it whenever the question names its own "
                        "source — it costs nothing and keeps unrelated hits out."
                    ),
                },
                "file_types": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Optional list of languages to filter by (e.g. go, python, typescript, markdown)",
                },
                "path_filter": {
                    "type": "string",
                    "description": "One indexed file (its file_path as returned in results) or a directory: every file under it",
                },
                "limit": {
                    "type": "integer",
                    "description": "Maximum number of results (default 5)",
                    "default": 5,
                },
                "tags_any": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Filter to chunks tagged with ANY of these (OR). Use for cross-cutting queries that span multiple sub-projects.",
                },
                "tags_all": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Filter to chunks tagged with ALL of these (AND). Combine with tags_any to narrow further.",
                },
                "mode": {
                    "type": "string",
                    "enum": ["preview", "full"],
                    "description": "preview (default) truncates each chunk's content to ~30 lines / 1500 chars and sets metadata.truncated=true; full returns the entire chunk. Start with preview, re-call with mode=full + path_filter once you've identified the relevant file.",
                    "default": "preview",
                },
            },
            "required": ["query"],
        },
    ),
    types.Tool(
        name="mnemos_search_code",
        description=(
            "Code-aware semantic search over the indexed codebase (Go AST chunks, "
            "Vue SFC blocks, fallback). Returns functions, types or sections — not "
            "whole files. Prefer this over mnemos_search for any 'where is X "
            "implemented / how does Y work' question. Workflow: keep the default "
            "limit=3 and mode=preview, scope with tags_any (project) and/or "
            "path_filter, then re-call with mode=full once you've identified the "
            "target file. metadata.truncated=true signals a preview was cut. "
            "Bumping limit past 5 usually means the query is too vague."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "The code search query"},
                "language": {
                    "type": "string",
                    "description": "Optional programming language filter (e.g. go, vue)",
                },
                "symbol_type": {
                    "type": "string",
                    "description": "Optional chunk type filter (e.g. function, type)",
                },
                "path_filter": {
                    "type": "string",
                    "description": "One indexed file (its file_path as returned in results) or a directory: every file under it",
                },
                "limit": {
                    "type": "integer",
                    "description": "Maximum number of results (default 3 — small on purpose; bump only if the first call clearly missed the right chunk).",
                    "default": 3,
                },
                "tags_any": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Filter to chunks tagged with ANY of these (OR). Use for cross-cutting queries that span multiple sub-projects.",
                },
                "tags_all": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Filter to chunks tagged with ALL of these (AND). Combine with tags_any to narrow further.",
                },
                "mode": {
                    "type": "string",
                    "enum": ["preview", "full"],
                    "description": "preview (default) truncates each chunk's content to ~30 lines / 1500 chars and sets metadata.truncated=true; full returns the entire chunk. Start with preview, re-call with mode=full + path_filter once you've identified the relevant file.",
                    "default": "preview",
                },
            },
            "required": ["query"],
        },
    ),
    types.Tool(
        name="mnemos_search_skills",
        description=(
            "Find Claude Code skills relevant to the user's task. Returns "
            "skill name + 200-char description preview. Use when you suspect a "
            "specialised skill exists (DevOps, DDD, frontend…) but you don't "
            "remember the exact slug. Already preview-only — no mode flag needed."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "The skill search query"},
                "limit": {
                    "type": "integer",
                    "description": "Maximum number of results (default 3)",
                    "default": 3,
                },
            },
            "required": ["query"],
        },
    ),
    types.Tool(
        name="mnemos_search_memory",
        description=(
            "Search approved memories (decisions, patterns, lessons, conventions) "
            "extracted from prior work. Only returns status='approved' entries. "
            "Use BEFORE re-deciding anything that smells like a recurring choice "
            "('which library', 'how do we handle X', past incidents). Default "
            "mode=preview is usually enough since memories are short; switch to "
            "mode=full only if a preview was truncated."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "The memory search query"},
                "memory_type": {
                    "type": "string",
                    "description": "Optional memory type filter",
                },
                "limit": {
                    "type": "integer",
                    "description": "Maximum number of results (default 5)",
                    "default": 5,
                },
                "tags_any": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Filter to chunks tagged with ANY of these (OR). Use for cross-cutting queries that span multiple sub-projects.",
                },
                "tags_all": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Filter to chunks tagged with ALL of these (AND). Combine with tags_any to narrow further.",
                },
                "mode": {
                    "type": "string",
                    "enum": ["preview", "full"],
                    "description": "preview (default) truncates each chunk's content to ~30 lines / 1500 chars and sets metadata.truncated=true; full returns the entire chunk. Start with preview, re-call with mode=full + path_filter once you've identified the relevant file.",
                    "default": "preview",
                },
            },
            "required": ["query"],
        },
    ),
    types.Tool(
        name="mnemos_memory",
        description="Store a new memory entry in the RAG memory collection.",
        inputSchema={
            "type": "object",
            "properties": {
                "content": {
                    "type": "string",
                    "description": "The memory content to store",
                },
                "project": {
                    "type": "string",
                    "description": "Optional project name for the memory",
                },
                "topic": {
                    "type": "string",
                    "description": "Optional topic label",
                },
                "memory_type": {
                    "type": "string",
                    "description": "Type of memory (e.g. decision, pattern, note)",
                    "default": "note",
                },
                "tags": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Optional tags for the memory",
                },
            },
            "required": ["content"],
        },
    ),
    types.Tool(
        name="mnemos_memory_list",
        description="List memory entries, optionally filtered by project or status.",
        inputSchema={
            "type": "object",
            "properties": {
                "project": {
                    "type": "string",
                    "description": "Optional project name filter",
                },
                "status": {
                    "type": "string",
                    "description": "Optional status filter (pending, approved, rejected)",
                },
                "limit": {
                    "type": "integer",
                    "description": "Maximum number of entries to return (default 20)",
                    "default": 20,
                },
            },
        },
    ),
    types.Tool(
        name="mnemos_memory_review",
        description="Approve or reject a pending memory entry.",
        inputSchema={
            "type": "object",
            "properties": {
                "memory_id": {
                    "type": "string",
                    "description": "The ID of the memory entry to review",
                },
                "action": {
                    "type": "string",
                    "enum": ["approve", "reject"],
                    "description": "Whether to approve or reject the memory",
                },
            },
            "required": ["memory_id", "action"],
        },
    ),
    types.Tool(
        name="mnemos_reindex",
        description=(
            "Re-index a file or a directory tree into a collection, in the background. "
            "Needs a key that may write every tag. Returns once the run has started; "
            "the semantic cache is cleared when it starts and when it ends."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "collection": {
                    "type": "string",
                    "enum": ["mnemos_code", "mnemos_skills", "mnemos_docs"],
                    "description": "Collection to fill. Memory and knowledge are pushed, not walked.",
                },
                "path": {
                    "type": "string",
                    "description": (
                        "Server-side path of a file or directory, under the codebase or the Claude "
                        "config mount. Default: the collection's root (the whole codebase, skills/ or docs/)."
                    ),
                },
            },
            "required": ["collection"],
        },
    ),
    types.Tool(
        name="mnemos_status",
        description="Get the current status of all RAG collections and the indexer.",
        inputSchema={
            "type": "object",
            "properties": {},
        },
    ),
]


def create_mcp_server(
    search_service: SearchService,
    indexer: Indexer,
    qdrant_client: QdrantClient | None = None,
    embedding_service: EmbeddingService | None = None,
    deduplicator=None,
    cache=None,
    audit_log=None,
    index_log=None,
) -> Server:
    """Create and configure an MCP server with all 9 Mnemos tools."""
    server = Server("mnemos-mcp")

    @server.list_tools()
    async def handle_list_tools() -> list[types.Tool]:
        return TOOL_DEFINITIONS

    @server.call_tool()
    async def handle_call_tool(
        name: str, arguments: dict[str, Any] | None
    ) -> list[types.TextContent]:
        args = arguments or {}
        try:
            principal = _caller(server)
            result = await _dispatch_tool(
                name, args, principal, search_service, indexer, qdrant_client, deduplicator, cache,
                audit_log=audit_log, index_log=index_log,
            )
            # Compact JSON: LLMs don't need pretty-printing. Roughly halves the
            # token cost of every MCP response.
            return [types.TextContent(
                type="text",
                text=json.dumps(result, separators=(",", ":"), ensure_ascii=False),
            )]
        except Exception as exc:
            return [types.TextContent(type="text", text=f"Error: {exc}")]

    return server


def _caller(server: Server) -> Principal:
    """Principal of the HTTP request carrying the current tool call.

    The streamable HTTP transport hands the Starlette request to the request
    context, a contextvar: read it here, on the event loop, before the tool
    hops to a worker thread. No request (direct call, stdio) means no
    credentials, which `principal_from_scope` refuses when auth is on.
    """
    try:
        request = server.request_context.request
    except LookupError:
        request = None
    return principal_from_scope(request.scope if request is not None else {})


async def _dispatch_tool(
    name: str,
    args: dict[str, Any],
    principal: Principal,
    search_service: SearchService,
    indexer: Indexer,
    qdrant_client: QdrantClient | None,
    deduplicator=None,
    cache=None,
    *,
    audit_log=None,
    index_log=None,
) -> Any:
    # Every tool blocks (torch, sync Qdrant, LLM): run it off the event loop so
    # one slow call does not stall the other MCP sessions and REST requests.
    return await anyio.to_thread.run_sync(
        functools.partial(
            _run_tool, name, args, principal, search_service, indexer, qdrant_client, deduplicator, cache,
            audit_log=audit_log, index_log=index_log,
        )
    )


# Tools that change something, recorded in the audit trail like their REST twins.
_AUDITED_TOOLS = {
    "mnemos_memory": "memory.create",
    "mnemos_memory_review": "memory.review",
    "mnemos_reindex": "index.reindex",
}


def _run_tool(
    name: str,
    args: dict[str, Any],
    principal: Principal,
    search_service: SearchService,
    indexer: Indexer,
    qdrant_client: QdrantClient | None,
    deduplicator=None,
    cache=None,
    *,
    audit_log=None,
    index_log=None,
) -> Any:
    action = _AUDITED_TOOLS.get(name)
    run = functools.partial(
        _tool, name, args, principal, search_service, indexer, qdrant_client, deduplicator, cache,
        index_log=index_log,
    )
    if action is None:
        return run()
    target = args.get("memory_id") or f"{args.get('collection', '')}:{args.get('path') or ''}"
    try:
        result = run()
    except HTTPException as exc:
        if exc.status_code == 403:
            audit_record(audit_log, actor=principal.name, action=action, target=target,
                         outcome="denied", via="mcp", detail=str(exc.detail))
        raise
    if name == "mnemos_memory":
        target = result["id"]
    changes = {k: v for k, v in args.items() if k != "content"}
    audit_record(audit_log, actor=principal.name, action=action, target=target, changes=changes, via="mcp")
    return result


def _tool(
    name: str,
    args: dict[str, Any],
    principal: Principal,
    search_service: SearchService,
    indexer: Indexer,
    qdrant_client: QdrantClient | None,
    deduplicator=None,
    cache=None,
    *,
    index_log=None,
) -> Any:
    if name == "mnemos_search":
        results = search_service.search(
            query=args["query"],
            collections=args.get("collections"),
            file_types=args.get("file_types"),
            path_filter=args.get("path_filter"),
            limit=args.get("limit", 5),
            tags_any=args.get("tags_any"),
            tags_all=args.get("tags_all"),
            mode=args.get("mode", "preview"),
            read_scope=principal.read_filter(),
        )
        return _apply_response_budget([r.model_dump() for r in results])

    if name == "mnemos_search_code":
        results = search_service.search_code(
            query=args["query"],
            language=args.get("language"),
            symbol_type=args.get("symbol_type"),
            path_filter=args.get("path_filter"),
            limit=args.get("limit", 3),
            tags_any=args.get("tags_any"),
            tags_all=args.get("tags_all"),
            mode=args.get("mode", "preview"),
            read_scope=principal.read_filter(),
        )
        return _apply_response_budget([r.model_dump() for r in results])

    if name == "mnemos_search_skills":
        results = search_service.search_skills(
            query=args["query"],
            limit=args.get("limit", 3),
        )
        return _apply_response_budget([r.model_dump() for r in results])

    if name == "mnemos_search_memory":
        results = search_service.search_memory(
            query=args["query"],
            memory_type=args.get("memory_type"),
            limit=args.get("limit", 5),
            tags_any=args.get("tags_any"),
            tags_all=args.get("tags_all"),
            mode=args.get("mode", "preview"),
            read_scope=principal.read_filter(),
        )
        return _apply_response_budget([r.model_dump() for r in results])

    if name == "mnemos_memory":
        if deduplicator is None:
            raise RuntimeError("deduplicator required for mnemos_memory")
        from core.models import ExtractedMemory
        tags = args.get("tags", [])
        if not principal.can_write_declared(tags):
            raise forbidden(f"Tenant {principal.name!r} cannot write tags {sorted(set(tags))}")
        memory = ExtractedMemory(
            content=args["content"],
            memory_type=args.get("memory_type", "note"),
            project=args.get("project"),
            tags=tags,
        )
        result = deduplicator.deduplicate_and_store(memory)
        invalidate_cache(cache)
        return {"id": result.memory_id, "status": "approved", "action": result.action}

    if name == "mnemos_memory_list":
        if qdrant_client is None:
            raise RuntimeError("qdrant_client required for mnemos_memory_list")
        entries = _list_memory(
            qdrant_client=qdrant_client,
            project=args.get("project"),
            status=args.get("status"),
            limit=args.get("limit", 20),
            read_scope=principal.read_filter(),
        )
        return entries

    if name == "mnemos_memory_review":
        if qdrant_client is None:
            raise RuntimeError("qdrant_client required for mnemos_memory_review")
        _review_memory(
            qdrant_client=qdrant_client,
            memory_id=args["memory_id"],
            action=args["action"],
            principal=principal,
        )
        invalidate_cache(cache)
        return {"memory_id": args["memory_id"], "action": args["action"]}

    if name == "mnemos_reindex":
        require_write_all(principal)
        collection = args["collection"]
        root = args.get("path") or default_reindex_root(collection)
        if root is None:
            raise ValueError(f"{collection} is filled by pushes, not by a walk: name a path")
        base_path = validate_path(root)
        if not base_path.exists():
            raise ValueError(f"Not found on the server: {root}")
        indexer.ensure_collection(collection)
        invalidate_cache(cache)
        job = ReindexJob(index_log, collection=collection, path=str(base_path), full=base_path.is_dir(),
                         actor=principal.name, channel="mcp")
        threading.Thread(
            target=run_reindex,
            args=(indexer, collection, base_path, base_path.is_dir()),
            kwargs={"cache": cache, "job": job},
            name=f"reindex-{collection}",
            daemon=True,
        ).start()
        return {"status": "reindex_started", "collection": collection, "path": str(base_path), "job_id": job.id}

    if name == "mnemos_status":
        if qdrant_client is None:
            return {"error": "qdrant_client not available"}
        return _get_status(qdrant_client)

    raise ValueError(f"Unknown tool: {name}")


def _list_memory(
    # NOTE: memory write/list still uses the legacy `project` payload field.
    # The memory pipeline migration to `tags` is tracked as a follow-up — see
    # ROADMAP.md. `mnemos_memory_list` filters here, `mnemos_search_memory`
    # filters by tags via SearchService — the inconsistency is intentional
    # until the memory write side is migrated.
    qdrant_client: QdrantClient,
    project: str | None,
    status: str | None,
    limit: int,
    read_scope: list[str] | None,
) -> list[dict]:
    from qdrant_client.models import Filter, FieldCondition, MatchAny, MatchValue

    must_conditions = []
    if read_scope is not None:
        must_conditions.append(FieldCondition(key="tags", match=MatchAny(any=read_scope)))
    if project:
        must_conditions.append(
            FieldCondition(key="project", match=MatchValue(value=project))
        )
    if status:
        must_conditions.append(
            FieldCondition(key="status", match=MatchValue(value=status))
        )

    query_filter = Filter(must=must_conditions) if must_conditions else None
    records, _ = qdrant_client.scroll(
        collection_name="mnemos_memory",
        scroll_filter=query_filter,
        limit=limit,
        with_payload=True,
    )
    return [r.payload for r in records]


def _review_memory(
    qdrant_client: QdrantClient,
    memory_id: str,
    action: str,
    principal: Principal,
) -> None:
    if not principal.write_all:
        records = qdrant_client.retrieve(
            collection_name="mnemos_memory", ids=[memory_id], with_payload=["tags"]
        )
        if not records:
            raise ValueError(f"Memory entry {memory_id!r} not found")
        tags = (records[0].payload or {}).get("tags") or []
        if not principal.can_write_resolved(tags):
            raise forbidden(f"Tenant {principal.name!r} cannot review memory {memory_id!r} (tags {tags})")

    new_status = "approved" if action == "approve" else "rejected"
    qdrant_client.set_payload(
        collection_name="mnemos_memory",
        payload={"status": new_status},
        points=[memory_id],
    )


def _get_status(qdrant_client: QdrantClient) -> dict:
    from core.indexer import dense_vector_size

    status: dict = {"collections": {}, "qdrant_healthy": False}
    try:
        qdrant_client.get_collections()
        status["qdrant_healthy"] = True
        for config in COLLECTIONS:
            # collection_exists, not get_collections(): the latter skips aliases.
            if qdrant_client.collection_exists(config.name):
                status["collections"][config.name] = {
                    "document_count": qdrant_client.get_collection(config.name).points_count or 0,
                    "vector_size": dense_vector_size(qdrant_client, config.name),
                }
            else:
                status["collections"][config.name] = {"document_count": 0, "vector_size": None}
    except Exception as exc:
        status["error"] = str(exc)
    return status
