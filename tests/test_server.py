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


@pytest.mark.anyio
async def test_theme_css_serves_the_instance_file(app, tmp_path, monkeypatch):
    from server.config import settings

    theme = tmp_path / "theme.css"
    theme.write_text(":root { --primary: oklch(0.6 0.2 250); }")
    monkeypatch.setattr(settings, "mnemos_ui_theme_path", str(theme))

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/theme.css")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/css")
    assert response.headers["cache-control"] == "no-cache"
    assert "--primary" in response.text


@pytest.mark.anyio
async def test_theme_css_absent_is_an_empty_stylesheet(app, tmp_path, monkeypatch):
    from server.config import settings

    monkeypatch.setattr(settings, "mnemos_ui_theme_path", str(tmp_path / "missing.css"))

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/theme.css")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/css")
    assert response.text == ""


def test_theme_css_is_not_shadowed_by_the_dashboard_mount(tmp_path, monkeypatch):
    """The root StaticFiles mount serves index.html for unknown paths."""
    from fastapi.testclient import TestClient
    from server.config import settings

    ui = tmp_path / "ui"
    ui.mkdir()
    (ui / "index.html").write_text("<html>dashboard</html>")
    monkeypatch.setattr(settings, "mnemos_ui_path", str(ui))
    monkeypatch.setattr(settings, "mnemos_ui_theme_path", str(tmp_path / "missing.css"))

    with patch("server.main.QdrantClient"):
        client = TestClient(create_app())
    response = client.get("/theme.css")
    assert response.headers["content-type"].startswith("text/css")
    assert response.text == ""


@pytest.mark.parametrize("path", ["/mcp", "/mcp/"])
def test_mcp_answers_with_or_without_slash_beside_the_dashboard(tmp_path, monkeypatch, path):
    """The dashboard mounted at `/` used to swallow bare `/mcp` with a 405."""
    from fastapi.testclient import TestClient
    from server.config import settings

    ui = tmp_path / "ui"
    ui.mkdir()
    (ui / "index.html").write_text("<html>dashboard</html>")
    monkeypatch.setattr(settings, "mnemos_ui_path", str(ui))

    with patch("server.main.QdrantClient"):
        client = TestClient(create_app())
    # No lifespan here: reaching the MCP handler shows as its 503, not the UI's 405.
    response = client.post(path, json={})
    assert response.status_code == 503
    assert response.text == "MCP server not initialised"
