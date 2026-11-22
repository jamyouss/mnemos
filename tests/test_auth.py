from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from server.auth import TenantsConfigError, load_api_keys
from server.config import settings
from server.main import create_app


def _write_tenants(tmp_path, body: str):
    path = tmp_path / "tenants.yaml"
    path.write_text(body)
    return path


# ---------------------------------------------------------------------------
# load_api_keys
# ---------------------------------------------------------------------------


def test_load_api_keys_maps_each_key_to_its_tenant(tmp_path):
    path = _write_tenants(tmp_path, """
tenants:
  team_a:
    api_key: "key-a"
  team_b:
    api_key: "key-b"
""")
    assert load_api_keys(path) == {"key-a": "team_a", "key-b": "team_b"}


def test_load_api_keys_refuses_missing_file(tmp_path):
    with pytest.raises(TenantsConfigError, match="not found"):
        load_api_keys(tmp_path / "absent.yaml")


def test_load_api_keys_refuses_a_file_without_tenants(tmp_path):
    path = _write_tenants(tmp_path, "tenants: {}\n")
    with pytest.raises(TenantsConfigError, match="no tenant"):
        load_api_keys(path)


@pytest.mark.parametrize("key", ['""', '"CHANGE_ME"', "null"])
def test_load_api_keys_refuses_placeholder_or_empty_keys(tmp_path, key):
    path = _write_tenants(tmp_path, f"tenants:\n  team_a:\n    api_key: {key}\n")
    with pytest.raises(TenantsConfigError, match="team_a"):
        load_api_keys(path)


def test_load_api_keys_refuses_a_key_shared_by_two_tenants(tmp_path):
    path = _write_tenants(tmp_path, """
tenants:
  team_a:
    api_key: "same"
  team_b:
    api_key: "same"
""")
    with pytest.raises(TenantsConfigError, match="shared"):
        load_api_keys(path)


# ---------------------------------------------------------------------------
# Middleware wired by create_app
# ---------------------------------------------------------------------------


@pytest.fixture
def auth_app(tmp_path, monkeypatch):
    tenants = _write_tenants(tmp_path, "tenants:\n  team_a:\n    api_key: \"secret-a\"\n")
    monkeypatch.setattr(settings, "mnemos_auth_enabled", True)
    monkeypatch.setattr(settings, "mnemos_tenants_path", str(tenants))
    monkeypatch.setattr(settings, "qdrant_api_key", "qdrant-key")
    with patch("server.main.QdrantClient") as qdrant_cls:
        qdrant = MagicMock()
        qdrant.get_collections.return_value.collections = []
        qdrant_cls.return_value = qdrant
        app = create_app()
        app.state.qdrant = qdrant
        yield app


async def _get(app, path: str, headers: dict | None = None):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        return await client.get(path, headers=headers or {})


@pytest.mark.anyio
async def test_api_rejects_requests_without_a_key(auth_app):
    response = await _get(auth_app, "/api/status")
    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"


@pytest.mark.anyio
async def test_api_rejects_an_unknown_key(auth_app):
    response = await _get(auth_app, "/api/status", {"Authorization": "Bearer wrong"})
    assert response.status_code == 401


@pytest.mark.anyio
async def test_api_accepts_a_known_key(auth_app):
    response = await _get(auth_app, "/api/status", {"Authorization": "Bearer secret-a"})
    assert response.status_code == 200


@pytest.mark.anyio
@pytest.mark.parametrize("path", ["/mcp/", "/internal/reindex"])
async def test_mcp_and_internal_routes_are_protected(auth_app, path):
    response = await _get(auth_app, path)
    assert response.status_code == 401


@pytest.mark.anyio
async def test_health_stays_open_for_probes(auth_app):
    response = await _get(auth_app, "/health")
    assert response.status_code == 200


@pytest.mark.anyio
async def test_prefix_lookalike_paths_are_not_mistaken_for_api(auth_app):
    # /apix is not under /api: the guard must match whole path segments.
    response = await _get(auth_app, "/apix")
    assert response.status_code != 401


def test_create_app_fails_closed_when_tenants_are_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "mnemos_auth_enabled", True)
    monkeypatch.setattr(settings, "mnemos_tenants_path", str(tmp_path / "absent.yaml"))
    monkeypatch.setattr(settings, "qdrant_api_key", "qdrant-key")
    with pytest.raises(TenantsConfigError):
        create_app()


def test_create_app_refuses_auth_in_front_of_an_open_qdrant(tmp_path, monkeypatch):
    tenants = _write_tenants(tmp_path, "tenants:\n  team_a:\n    api_key: \"secret-a\"\n")
    monkeypatch.setattr(settings, "mnemos_auth_enabled", True)
    monkeypatch.setattr(settings, "mnemos_tenants_path", str(tenants))
    monkeypatch.setattr(settings, "qdrant_api_key", "")
    with pytest.raises(RuntimeError, match="QDRANT_API_KEY"):
        create_app()


@pytest.mark.anyio
async def test_auth_disabled_keeps_the_api_open(monkeypatch):
    monkeypatch.setattr(settings, "mnemos_auth_enabled", False)
    with patch("server.main.QdrantClient"):
        app = create_app()
    app.state.qdrant = MagicMock()
    app.state.qdrant.get_collections.return_value.collections = []
    response = await _get(app, "/api/status")
    assert response.status_code == 200
