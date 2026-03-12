"""Integration tests for MCP SSE transport mounted on FastAPI."""
import pytest
from unittest.mock import patch, MagicMock
from httpx import AsyncClient, ASGITransport

from server.main import create_app


@pytest.fixture
def app():
    with patch("server.main.QdrantClient") as mock_qdrant_cls:
        mock_qdrant_cls.return_value = MagicMock()
        mock_qdrant_cls.return_value.get_collections.return_value.collections = []
        yield create_app()


@pytest.mark.anyio
async def test_mcp_sse_endpoint_exists(app):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # The SSE endpoint should exist (not 404).
        # With lifespan not triggered in unit tests, the endpoint may raise a 500
        # because app.state is not initialised — that is still not a 404.
        response = await client.get("/mcp/sse", timeout=2)
        assert response.status_code != 404


@pytest.mark.anyio
async def test_mcp_messages_endpoint_exists(app):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # POST to messages without session_id should return 400, not 404
        response = await client.post("/mcp/messages/", content=b"{}")
        assert response.status_code != 404


@pytest.mark.anyio
async def test_health_still_works_after_mcp_mount(app):
    """Health endpoint must remain functional after mounting MCP."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/health")
        assert response.status_code == 200
        assert response.json()["status"] == "healthy"
