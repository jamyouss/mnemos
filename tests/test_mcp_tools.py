"""Tests for MCP tool definitions."""
import asyncio
from unittest.mock import MagicMock

import mcp.types as types

from server.mcp_tools import TOOL_DEFINITIONS, _dispatch_tool, create_mcp_server


def test_mcp_server_has_all_tools():
    search_service = MagicMock()
    indexer = MagicMock()
    mcp_server = create_mcp_server(search_service=search_service, indexer=indexer)

    # The list_tools() decorator is registered on the server; verify via TOOL_DEFINITIONS
    # which is the canonical source used by the list_tools handler.
    tool_names = {tool.name for tool in TOOL_DEFINITIONS}
    expected = {
        "mnemos_search",
        "mnemos_search_code",
        "mnemos_search_skills",
        "mnemos_search_memory",
        "mnemos_memory",
        "mnemos_memory_list",
        "mnemos_memory_review",
        "mnemos_reindex",
        "mnemos_status",
    }
    assert expected.issubset(tool_names)


def test_all_tools_have_valid_schemas():
    """Each tool must have a name, description, and a valid inputSchema object."""
    for tool in TOOL_DEFINITIONS:
        assert tool.name, f"Tool missing name: {tool}"
        assert tool.description, f"Tool {tool.name} missing description"
        assert isinstance(tool.inputSchema, dict), f"Tool {tool.name} inputSchema must be dict"
        assert tool.inputSchema.get("type") == "object", (
            f"Tool {tool.name} inputSchema must have type=object"
        )


def test_required_tools_have_required_fields():
    """Tools that need a query or key parameter must list it as required."""
    query_tools = {
        "mnemos_search", "mnemos_search_code", "mnemos_search_skills", "mnemos_search_memory"
    }
    tool_map = {t.name: t for t in TOOL_DEFINITIONS}
    for name in query_tools:
        assert name in tool_map, f"Tool {name} not found"
        required = tool_map[name].inputSchema.get("required", [])
        assert "query" in required, f"Tool {name} should require 'query'"

    review_tool = tool_map["mnemos_memory_review"]
    required = review_tool.inputSchema.get("required", [])
    assert "memory_id" in required
    assert "action" in required


def test_mcp_server_registers_list_tools_handler():
    """Verify the server has a handler for ListToolsRequest after setup."""
    search_service = MagicMock()
    indexer = MagicMock()
    mcp_server = create_mcp_server(search_service=search_service, indexer=indexer)

    assert types.ListToolsRequest in mcp_server.request_handlers, (
        "Server must register a list_tools handler"
    )


def test_mcp_server_registers_call_tool_handler():
    """Verify the server has a handler for CallToolRequest after setup."""
    search_service = MagicMock()
    indexer = MagicMock()
    mcp_server = create_mcp_server(search_service=search_service, indexer=indexer)

    assert types.CallToolRequest in mcp_server.request_handlers, (
        "Server must register a call_tool handler"
    )


def _schema_props(tool_name: str) -> dict:
    tool = next(t for t in TOOL_DEFINITIONS if t.name == tool_name)
    return tool.inputSchema.get("properties", {})


def test_search_tool_exposes_tags_filters():
    """mnemos_search schema should declare tags_any and tags_all as arrays of strings."""
    props = _schema_props("mnemos_search")
    assert "tags_any" in props, "mnemos_search must expose tags_any"
    assert "tags_all" in props, "mnemos_search must expose tags_all"
    assert props["tags_any"]["type"] == "array"
    assert props["tags_any"]["items"]["type"] == "string"
    assert props["tags_all"]["type"] == "array"
    assert props["tags_all"]["items"]["type"] == "string"


def test_search_code_tool_exposes_tags_and_drops_project():
    """mnemos_search_code schema must use tags_any/tags_all and not legacy project."""
    props = _schema_props("mnemos_search_code")
    assert "tags_any" in props
    assert "tags_all" in props
    assert props["tags_any"]["type"] == "array"
    assert props["tags_any"]["items"]["type"] == "string"
    assert props["tags_all"]["type"] == "array"
    assert props["tags_all"]["items"]["type"] == "string"
    assert "project" not in props, "mnemos_search_code should no longer expose project"


def test_search_memory_tool_exposes_tags_and_drops_project():
    """mnemos_search_memory schema must use tags_any/tags_all and not legacy project."""
    props = _schema_props("mnemos_search_memory")
    assert "tags_any" in props
    assert "tags_all" in props
    assert props["tags_any"]["type"] == "array"
    assert props["tags_any"]["items"]["type"] == "string"
    assert props["tags_all"]["type"] == "array"
    assert props["tags_all"]["items"]["type"] == "string"
    assert "project" not in props, "mnemos_search_memory should no longer expose project"


def test_dispatch_search_code_forwards_tag_filters():
    """Dispatching mnemos_search_code with tag args must forward both kwargs and NOT project."""
    search_service = MagicMock()
    search_service.search_code.return_value = []
    indexer = MagicMock()

    asyncio.run(
        _dispatch_tool(
            name="mnemos_search_code",
            args={
                "query": "hybrid retrieval",
                "tags_any": ["moby", "trevio"],
                "tags_all": ["go"],
            },
            search_service=search_service,
            indexer=indexer,
            qdrant_client=None,
        )
    )

    search_service.search_code.assert_called_once()
    kwargs = search_service.search_code.call_args.kwargs
    assert kwargs["query"] == "hybrid retrieval"
    assert kwargs["tags_any"] == ["moby", "trevio"]
    assert kwargs["tags_all"] == ["go"]
    assert "project" not in kwargs, "Dispatcher must not forward the legacy project kwarg"


def test_dispatch_search_memory_forwards_tag_filters():
    """Dispatching mnemos_search_memory with tag args must forward both kwargs and NOT project."""
    search_service = MagicMock()
    search_service.search_memory.return_value = []
    indexer = MagicMock()

    asyncio.run(
        _dispatch_tool(
            name="mnemos_search_memory",
            args={
                "query": "past decisions",
                "tags_any": ["moby"],
                "tags_all": ["lesson"],
            },
            search_service=search_service,
            indexer=indexer,
            qdrant_client=None,
        )
    )

    search_service.search_memory.assert_called_once()
    kwargs = search_service.search_memory.call_args.kwargs
    assert kwargs["query"] == "past decisions"
    assert kwargs["tags_any"] == ["moby"]
    assert kwargs["tags_all"] == ["lesson"]
    assert "project" not in kwargs, "Dispatcher must not forward the legacy project kwarg"


def test_dispatch_search_forwards_tag_filters():
    """Dispatching mnemos_search with tag args must forward both kwargs."""
    search_service = MagicMock()
    search_service.search.return_value = []
    indexer = MagicMock()

    asyncio.run(
        _dispatch_tool(
            name="mnemos_search",
            args={
                "query": "anything",
                "tags_any": ["a"],
                "tags_all": ["b"],
            },
            search_service=search_service,
            indexer=indexer,
            qdrant_client=None,
        )
    )

    search_service.search.assert_called_once()
    kwargs = search_service.search.call_args.kwargs
    assert kwargs["query"] == "anything"
    assert kwargs["tags_any"] == ["a"]
    assert kwargs["tags_all"] == ["b"]
