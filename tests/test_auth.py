from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from server.auth import (
    BearerAuthMiddleware,
    TenantsConfigError,
    TenantTable,
    hash_key,
    load_api_keys,
    parse_scope,
    principal_of_tenant,
)
from server.config import settings
from server.main import create_app
from server.principal import PRINCIPAL_SCOPE_KEY, Principal
from tests.config_fakes import InMemoryConfigQdrant, boot, doc_store, isolate_settings


def _write_tenants(tmp_path, body: str):
    path = tmp_path / "tenants.yaml"
    path.write_text(body)
    return path


def _entry(key: str, admin: bool = False, read="*", write="*") -> dict:
    return {"key_sha256": hash_key(key), "admin": admin, "read": read, "write": write,
            "created_at": ""}


# ---------------------------------------------------------------------------
# load_api_keys (tenants.yaml, validated before the bootstrap import)
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
# Tenant model
# ---------------------------------------------------------------------------


def test_hash_key_is_sha256_hex():
    assert hash_key("abc") == "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"


@pytest.mark.parametrize("value,expected", [
    ("*", "*"),
    ([" b ", "a", "a", ""], ["a", "b"]),
    ([], []),
])
def test_parse_scope_normalises(value, expected):
    assert parse_scope(value) == expected


@pytest.mark.parametrize("value", [None, "acme", ["ok", 3], {"a": 1}])
def test_parse_scope_rejects_anything_else(value):
    with pytest.raises(ValueError):
        parse_scope(value)


def test_principal_of_tenant_maps_star_to_every_tag():
    principal = principal_of_tenant("ci", _entry("k", read="*", write=["acme"]))
    assert principal == Principal(name="ci", admin=False, read=None, write=frozenset({"acme"}))


def test_tenant_table_is_replaced_whole():
    table = TenantTable()
    table.replace({"tenants": {"a": _entry("key-a", admin=True)}})
    assert table.lookup("key-a").name == "a"
    table.replace({"tenants": {"b": _entry("key-b")}})
    assert table.lookup("key-a") is None
    assert table.lookup("key-b").name == "b"
    assert len(table) == 1


# ---------------------------------------------------------------------------
# Middleware
# ---------------------------------------------------------------------------


async def _call(table: TenantTable, path: str, headers: dict | None = None):
    seen = {}

    async def inner(scope, receive, send):
        seen["principal"] = scope.get(PRINCIPAL_SCOPE_KEY)
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"ok"})

    transport = ASGITransport(app=BearerAuthMiddleware(inner, table))
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get(path, headers=headers or {})
    return response, seen.get("principal")


@pytest.fixture
def table():
    table = TenantTable()
    table.replace({"tenants": {"team_a": _entry("secret-a", read=["acme"], write=[])}})
    return table


@pytest.mark.anyio
async def test_middleware_stores_the_principal_in_the_scope(table):
    response, principal = await _call(table, "/api/status", {"Authorization": "Bearer secret-a"})
    assert response.status_code == 200
    assert principal == Principal("team_a", False, frozenset({"acme"}), frozenset())


@pytest.mark.anyio
@pytest.mark.parametrize("headers", [{}, {"Authorization": "Bearer wrong"},
                                     {"Authorization": "Basic secret-a"}])
async def test_middleware_rejects_missing_or_unknown_keys(table, headers):
    response, principal = await _call(table, "/api/status", headers)
    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"
    assert principal is None


@pytest.mark.anyio
@pytest.mark.parametrize("path", ["/mcp/", "/internal/reindex", "/api"])
async def test_mcp_internal_and_api_are_protected(table, path):
    response, _ = await _call(table, path)
    assert response.status_code == 401


@pytest.mark.anyio
@pytest.mark.parametrize("path", ["/health", "/apix", "/", "/theme.css"])
async def test_open_paths_pass_without_a_key(table, path):
    response, principal = await _call(table, path)
    assert response.status_code == 200
    assert principal is None


# ---------------------------------------------------------------------------
# Wired by create_app
# ---------------------------------------------------------------------------


@pytest.fixture
def stored_tenants(tmp_path, monkeypatch):
    isolate_settings(monkeypatch, tmp_path, mnemos_auth_enabled=True, qdrant_api_key="qk")
    backend = InMemoryConfigQdrant()
    doc_store(backend).put("tenants", {"tenants": {"team_a": _entry("secret-a", admin=True)}})
    return backend


def test_app_loads_tenants_from_the_database(stored_tenants, monkeypatch):
    with boot(monkeypatch, stored_tenants) as client:
        assert client.get("/api/whoami").status_code == 401
        resp = client.get("/api/whoami", headers={"Authorization": "Bearer secret-a"})
        assert resp.status_code == 200
        assert resp.json() == {"name": "team_a", "admin": True, "read": "*", "write": "*",
                               "auth_enabled": True}
        assert client.get("/health").status_code == 200


def test_app_refuses_to_start_with_auth_and_no_tenant(tmp_path, monkeypatch):
    isolate_settings(monkeypatch, tmp_path, mnemos_auth_enabled=True, qdrant_api_key="qk")
    with pytest.raises(TenantsConfigError, match="no tenant"):
        with boot(monkeypatch, InMemoryConfigQdrant()):
            pass


def test_app_imports_the_tenants_file_on_first_start(tmp_path, monkeypatch):
    tenants = _write_tenants(tmp_path, "tenants:\n  team_a:\n    api_key: \"secret-a\"\n")
    isolate_settings(monkeypatch, tmp_path, mnemos_auth_enabled=True, qdrant_api_key="qk",
                     mnemos_tenants_path=str(tenants))
    with boot(monkeypatch, InMemoryConfigQdrant()) as client:
        resp = client.get("/api/whoami", headers={"Authorization": "Bearer secret-a"})
        assert resp.json()["name"] == "team_a"


def test_create_app_refuses_auth_in_front_of_an_open_qdrant(tmp_path, monkeypatch):
    isolate_settings(monkeypatch, tmp_path, mnemos_auth_enabled=True, qdrant_api_key="")
    with pytest.raises(RuntimeError, match="QDRANT_API_KEY"):
        create_app()


def test_auth_disabled_keeps_the_api_open(tmp_path, monkeypatch):
    isolate_settings(monkeypatch, tmp_path)
    assert settings.mnemos_auth_enabled is False
    with boot(monkeypatch, InMemoryConfigQdrant()) as client:
        resp = client.get("/api/whoami")
        assert resp.status_code == 200
        assert resp.json() == {"name": "anonymous", "admin": True, "read": "*", "write": "*",
                               "auth_enabled": False}
