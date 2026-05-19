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

# Registry of all tool definitions, used in list_tools and for testing
TOOL_DEFINITIONS: list[types.Tool] = [
    types.Tool(
        name="mnemos_search",
        description="Search across all indexed collections (docs, skills, code) using semantic similarity.",
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
            },
            "required": ["query"],
        },
    ),
    types.Tool(
        name="mnemos_search_code",
        description="Search code-specific collections for functions, types, or logic.",
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
            },
            "required": ["query"],
        },
    ),
    types.Tool(
        name="mnemos_search_skills",
        description="Search Claude Code skills by semantic similarity.",
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
        description="Search approved memory entries for past decisions and context.",
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
        )
        return [r.model_dump() for r in results]

    if name == "mnemos_search_code":
        results = search_service.search_code(
            query=args["query"],
            language=args.get("language"),
            symbol_type=args.get("symbol_type"),
            path_filter=args.get("path_filter"),
            limit=args.get("limit", 5),
            tags_any=args.get("tags_any"),
            tags_all=args.get("tags_all"),
        )
        return [r.model_dump() for r in results]

    if name == "mnemos_search_skills":
        results = search_service.search_skills(
            query=args["query"],
            limit=args.get("limit", 3),
        )
        return [r.model_dump() for r in results]

    if name == "mnemos_search_memory":
        results = search_service.search_memory(
            query=args["query"],
            memory_type=args.get("memory_type"),
            limit=args.get("limit", 5),
            tags_any=args.get("tags_any"),
            tags_all=args.get("tags_all"),
        )
        return [r.model_dump() for r in results]

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
