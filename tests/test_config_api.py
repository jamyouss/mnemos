from __future__ import annotations

import pytest

from server.auth import hash_key
from server.config import settings
from tests.config_fakes import InMemoryConfigQdrant, boot, doc_store, isolate_settings

SENTINEL = "sk-ant-SENTINEL-do-not-leak-4242"


def _field(body: dict, key: str) -> dict:
    for section in body["sections"]:
        for field in section["fields"]:
            if field["key"] == key:
                return field
    raise KeyError(key)


@pytest.fixture
def make_client(tmp_path, monkeypatch):
    """Boot the app (lifespan included) on an in-memory config database."""
    monkeypatch.setenv("MNEMOS_LLM_API_KEY", SENTINEL)
    backend = InMemoryConfigQdrant()
    clients = []

    def build(writable: bool = True, overrides: dict | None = None, **extra):
        isolate_settings(monkeypatch, tmp_path, mnemos_ui_config_writable=writable,
                         mnemos_llm_api_key=SENTINEL, **extra)
        if overrides is not None:
            doc_store(backend).put("settings", overrides)
        client = boot(monkeypatch, backend).__enter__()
        clients.append(client)
        return client

    build.backend = backend
    yield build
    for client in clients:
        client.__exit__(None, None, None)


def test_get_config_shape(make_client):
    client = make_client(writable=False)
    resp = client.get("/api/config")
    assert resp.status_code == 200
    body = resp.json()
    assert body["writable"] is False
    assert body["revision"] == 0
    assert "overrides_path" not in body
    assert body["pending_restart"] == []
    assert [s["id"] for s in body["sections"]] == [
        "retrieval", "cache", "llm", "memory", "indexing", "observability",
        "infrastructure", "security",
    ]
    field = _field(body, "mnemos_reranker_enabled")
    assert set(field) == {
        "key", "env", "label", "description", "type", "options", "min", "max",
        "value", "default", "source", "editable", "apply", "is_set",
    }
    assert field["env"] == "MNEMOS_RERANKER_ENABLED"
    assert _field(body, "mnemos_rewriter_strategy")["options"] == ["expansion", "decompose", "hyde"]


def test_api_key_never_serialized(make_client):
    client = make_client()
    get = client.get("/api/config")
    patch_resp = client.patch("/api/config", json={"values": {"mnemos_cache_enabled": True}})
    rejected = client.patch("/api/config", json={"values": {"mnemos_llm_api_key": SENTINEL}})

    assert get.status_code == 200 and patch_resp.status_code == 200
    assert rejected.status_code == 422
    for resp in (get, patch_resp, rejected):
        assert SENTINEL not in resp.text
        assert "SENTINEL" not in resp.text
    key = _field(get.json(), "mnemos_llm_api_key")
    assert key["value"] is None and key["is_set"] is True and key["type"] == "secret"
    assert SENTINEL not in str(make_client.backend.points)


def test_patch_and_delete_are_forbidden_when_not_writable(make_client):
    client = make_client(writable=False, overrides={"mnemos_cache_enabled": True})
    resp = client.patch("/api/config", json={"values": {"mnemos_cache_enabled": False}})
    assert resp.status_code == 403
    assert "detail" in resp.json()
    assert client.delete("/api/config/mnemos_cache_enabled").status_code == 403
    assert doc_store(make_client.backend).get("settings").data == {"mnemos_cache_enabled": True}


def test_patch_validation_is_all_or_nothing(make_client):
    client = make_client()
    resp = client.patch("/api/config", json={"values": {
        "mnemos_cache_enabled": True,          # valid
        "mnemos_mmr_lambda": 2,                # out of range
        "mnemos_rewriter_strategy": "magic",   # bad enum
        "mnemos_grader_enabled": "yes",        # wrong type
        "qdrant_host": "elsewhere",            # not editable
        "made_up": 1,                          # unknown
    }})
    assert resp.status_code == 422
    body = resp.json()
    assert "detail" in body
    assert set(body["errors"]) == {
        "mnemos_mmr_lambda", "mnemos_rewriter_strategy", "mnemos_grader_enabled",
        "qdrant_host", "made_up",
    }
    assert doc_store(make_client.backend).get("settings") is None
    assert client.app.state.cache.enabled is False


def test_live_patch_flips_the_running_cache(make_client):
    client = make_client()
    resp = client.patch("/api/config", json={"values": {
        "mnemos_cache_enabled": True, "mnemos_cache_ttl_seconds": 10,
    }})
    assert resp.status_code == 200
    body = resp.json()
    assert body["applied"] == ["mnemos_cache_enabled", "mnemos_cache_ttl_seconds"]
    assert body["revision"] == 1
    field = _field(body, "mnemos_cache_enabled")
    assert (field["value"], field["source"]) == (True, "override")
    cache = client.app.state.cache
    assert cache.enabled is True and cache.ttl_seconds == 10


def test_live_patch_reaches_search_service_and_reranker(make_client):
    client = make_client()
    resp = client.patch("/api/config", json={"values": {
        "mnemos_mmr_enabled": True, "mnemos_mmr_lambda": 0.7, "mnemos_reranker_enabled": True,
    }})
    assert resp.status_code == 200
    service = client.app.state.search_service
    assert (service.mmr_enabled, service.mmr_lambda) == (True, 0.7)
    assert client.app.state.reranker.enabled is True


def test_query_log_toggle_is_reflected_in_query_log_endpoint(make_client, tmp_path):
    client = make_client()
    assert client.get("/api/query-log").json()["enabled"] is False

    resp = client.patch("/api/config", json={"values": {"mnemos_query_log_enabled": True}})
    assert resp.status_code == 200
    assert client.get("/api/query-log").json()["enabled"] is True
    assert (tmp_path / "logs").is_dir()  # created like the constructor does


def test_restart_key_lands_in_pending_restart(make_client):
    client = make_client()
    resp = client.patch("/api/config", json={"values": {"mnemos_llm_model": "qwen2.5:7b"}})
    assert resp.status_code == 200
    body = resp.json()
    assert body["applied"] == []
    assert body["pending_restart"] == ["mnemos_llm_model"]
    assert settings.mnemos_llm_model != "qwen2.5:7b"
    assert client.get("/api/config").json()["pending_restart"] == ["mnemos_llm_model"]


def test_delete_reverts_override(make_client):
    client = make_client()
    client.patch("/api/config", json={"values": {"mnemos_cache_enabled": True}})

    resp = client.delete("/api/config/mnemos_cache_enabled")
    assert resp.status_code == 200
    field = _field(resp.json(), "mnemos_cache_enabled")
    assert field["source"] in ("env", "default")
    assert field["value"] is False
    assert client.app.state.cache.enabled is False

    assert client.delete("/api/config/mnemos_cache_enabled").status_code == 404


def test_stored_overrides_apply_at_startup(make_client):
    client = make_client(overrides={"mnemos_mmr_lambda": 0.25, "mnemos_llm_model": "phi3"})
    body = client.get("/api/config").json()
    assert _field(body, "mnemos_mmr_lambda")["source"] == "override"
    assert settings.mnemos_mmr_lambda == 0.25
    assert settings.mnemos_llm_model == "phi3"
    assert client.app.state.search_service.mmr_lambda == 0.25
    assert body["pending_restart"] == []
    assert body["revision"] == 1


def test_stale_revision_is_a_conflict(make_client):
    client = make_client(overrides={"mnemos_cache_enabled": True})
    resp = client.patch("/api/config", json={"values": {"mnemos_mmr_lambda": 0.3}, "revision": 0})
    assert resp.status_code == 409
    assert resp.json()["revision"] == 1 and "detail" in resp.json()

    resp = client.delete("/api/config/mnemos_cache_enabled", params={"revision": 0})
    assert resp.status_code == 409

    ok = client.patch("/api/config", json={"values": {"mnemos_mmr_lambda": 0.3}, "revision": 1})
    assert ok.status_code == 200 and ok.json()["revision"] == 2
    assert client.delete("/api/config/mnemos_cache_enabled", params={"revision": 2}).status_code == 200


def test_config_is_admin_only(make_client):
    tenants = {
        "root": {"key_sha256": hash_key("root"), "admin": True, "read": "*", "write": "*"},
        "dev": {"key_sha256": hash_key("dev"), "admin": False, "read": "*", "write": "*"},
    }
    doc_store(make_client.backend).put("tenants", {"tenants": tenants})
    client = make_client(mnemos_auth_enabled=True, qdrant_api_key="qk")
    dev = {"Authorization": "Bearer dev"}
    assert client.get("/api/config", headers=dev).status_code == 403
    assert client.patch("/api/config", headers=dev,
                        json={"values": {"mnemos_cache_enabled": True}}).status_code == 403
    assert client.delete("/api/config/mnemos_cache_enabled", headers=dev).status_code == 403
    assert client.get("/api/config", headers={"Authorization": "Bearer root"}).status_code == 200
