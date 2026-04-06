"""Tests for MCP tool definitions."""
from unittest.mock import MagicMock

import mcp.types as types

from server.mcp_tools import TOOL_DEFINITIONS, create_mcp_server


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
