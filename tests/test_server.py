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
async def test_health_endpoint(app):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/health")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "healthy"
