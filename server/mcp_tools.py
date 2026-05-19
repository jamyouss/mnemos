"""MCP tool definitions for the RAG server."""
from __future__ import annotations

import json
from typing import Any

import mcp.types as types
from mcp.server import Server
from qdrant_client import QdrantClient

from core.collections import COLLECTIONS
from core.embeddings import EmbeddingService
from core.indexer import Indexer
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
            "Broad semantic search across docs + skills + code. Use when the question "
            "spans multiple sources or you're not sure where the answer lives. For "
            "pure code lookups prefer mnemos_search_code (cheaper, code-aware). "
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
                    "description": "Optional list of collection names to search",
                },
                "file_types": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Optional list of file languages to filter by",
                },
                "path_filter": {
                    "type": "string",
                    "description": "Optional file path prefix filter",
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
                    "description": "Optional file path filter",
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
        description="Trigger re-indexing of a collection or all collections.",
        inputSchema={
            "type": "object",
            "properties": {
                "collection": {
                    "type": "string",
                    "description": "Optional specific collection name to reindex",
                },
                "mode": {
                    "type": "string",
                    "enum": ["incremental", "full"],
                    "description": "Reindex mode: incremental (default) or full",
                    "default": "incremental",
                },
            },
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
            result = await _dispatch_tool(name, args, search_service, indexer, qdrant_client, deduplicator)
            # Compact JSON: LLMs don't need pretty-printing. Roughly halves the
            # token cost of every MCP response.
            return [types.TextContent(
                type="text",
                text=json.dumps(result, separators=(",", ":"), ensure_ascii=False),
            )]
        except Exception as exc:
            return [types.TextContent(type="text", text=f"Error: {exc}")]

    return server


async def _dispatch_tool(
    name: str,
    args: dict[str, Any],
    search_service: SearchService,
    indexer: Indexer,
    qdrant_client: QdrantClient | None,
    deduplicator=None,
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
        )
        return _apply_response_budget([r.model_dump() for r in results])

    if name == "mnemos_memory":
        if deduplicator is None:
            raise RuntimeError("deduplicator required for mnemos_memory")
        from core.models import ExtractedMemory
        memory = ExtractedMemory(
            content=args["content"],
            memory_type=args.get("memory_type", "note"),
            project=args.get("project"),
            tags=args.get("tags", []),
        )
        result = deduplicator.deduplicate_and_store(memory)
        return {"id": result.memory_id, "status": "pending", "action": result.action}

    if name == "mnemos_memory_list":
        if qdrant_client is None:
            raise RuntimeError("qdrant_client required for mnemos_memory_list")
        entries = _list_memory(
            qdrant_client=qdrant_client,
            project=args.get("project"),
            status=args.get("status"),
            limit=args.get("limit", 20),
        )
        return entries

    if name == "mnemos_memory_review":
        if qdrant_client is None:
            raise RuntimeError("qdrant_client required for mnemos_memory_review")
        _review_memory(
            qdrant_client=qdrant_client,
            memory_id=args["memory_id"],
            action=args["action"],
        )
        return {"memory_id": args["memory_id"], "action": args["action"]}

    if name == "mnemos_reindex":
        return {
            "collection": args.get("collection"),
            "mode": args.get("mode", "incremental"),
            "message": "Reindex triggered (watcher-driven; call from watcher service for full execution)",
        }

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
) -> list[dict]:
    from qdrant_client.models import Filter, FieldCondition, MatchValue

    must_conditions = []
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
) -> None:
    from qdrant_client.models import SetPayload

    new_status = "approved" if action == "approve" else "rejected"
    qdrant_client.set_payload(
        collection_name="mnemos_memory",
        payload={"status": new_status},
        points=[memory_id],
    )


def _get_status(qdrant_client: QdrantClient) -> dict:
    from datetime import datetime, timezone

    status: dict = {"collections": {}, "qdrant_healthy": False}
    try:
        existing = {c.name: c for c in qdrant_client.get_collections().collections}
        status["qdrant_healthy"] = True
        for config in COLLECTIONS:
            if config.name in existing:
                coll_info = qdrant_client.get_collection(config.name)
                status["collections"][config.name] = {
                    "document_count": coll_info.points_count or 0,
                    "vector_size": config.vector_size,
                }
            else:
                status["collections"][config.name] = {
                    "document_count": 0,
                    "vector_size": config.vector_size,
                }
    except Exception as exc:
        status["error"] = str(exc)
    return status
