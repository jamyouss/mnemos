from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock

import pytest

from server.auth import hash_key
from server.ops_api import TenantUsage
from tests.config_fakes import InMemoryConfigQdrant, boot, doc_store, isolate_settings

ROOT = {"Authorization": "Bearer root-key"}
DEV = {"Authorization": "Bearer dev-key"}
JIRA = {"type": "jira", "name": "tracker", "url": "https://jira.example.com", "token_env": "T", "projects": ["OPS"]}


@pytest.fixture
def backend():
    backend = InMemoryConfigQdrant()
    doc_store(backend).put("tenants", {"tenants": {
        "root": {"key_sha256": hash_key("root-key"), "admin": True, "read": "*", "write": "*"},
        "dev": {"key_sha256": hash_key("dev-key"), "admin": False, "read": "*", "write": ["acme"]},
    }})
    return backend


@pytest.fixture
def client(tmp_path, monkeypatch, backend):
    isolate_settings(monkeypatch, tmp_path, mnemos_auth_enabled=True, qdrant_api_key="qk")
    monkeypatch.setattr("server.ops_api.health_deps", lambda request: {
        "qdrant": {"ok": True}, "llm": {"ok": True, "provider": "ollama", "error": None},
    })
    with boot(monkeypatch, backend) as client:
        client.app.state.index_events = MagicMock(query=MagicMock(return_value=([], None)))
        yield client


def _ids(client, headers=ROOT) -> list[str]:
    return [a["id"] for a in client.get("/api/alerts", headers=headers).json()["alerts"]]


def _ago(minutes: int) -> str:
    return (datetime.now(UTC) - timedelta(minutes=minutes)).isoformat(timespec="seconds")


def test_a_quiet_healthy_instance_has_no_alert(client):
    assert _ids(client) == []


def test_connectors_without_a_sync_service_then_a_silent_one(client, backend):
    doc_store(backend).put("connectors", {"sources": [JIRA]})
    assert _ids(client) == ["sync-never"]
    doc_store(backend).put("connectors_status", {"service_seen_at": _ago(10), "runs": {
        "tracker": {"state": "failed", "finished_at": _ago(10), "results": []},
    }})
    assert _ids(client) == ["sync-silent", "sync-failed"]
    # A key with no say over connectors sees none of it.
    assert _ids(client, DEV) == []


def test_the_watcher_is_silent_only_once_it_stopped_beating(client):
    assert client.post("/api/services/heartbeat", json={"name": "watcher"}, headers=ROOT).status_code == 200
    assert _ids(client) == []
    client.app.state.config_docs.put("services_status", {"watcher": {"seen_at": _ago(5)}})
    assert _ids(client) == ["watcher-silent"]
    assert client.post("/api/services/heartbeat", json={"name": "watcher"}, headers=DEV).status_code == 403


def test_an_unreachable_llm_is_an_alert_for_everyone(client, monkeypatch):
    monkeypatch.setattr("server.ops_api.health_deps", lambda request: {
        "qdrant": {"ok": True}, "llm": {"ok": False, "provider": "ollama", "error": "connection refused"},
    })
    assert _ids(client, DEV) == ["llm-down"]


def test_key_uses_from_two_pods_merge_latest_first(backend):
    store = doc_store(backend)
    a, b = TenantUsage(store), TenantUsage(store)
    a.touch("ci", "10.0.0.1")
    b.touch("bot", "10.0.0.2")
    a.flush()
    b.flush()
    assert set(a.read()) == {"ci", "bot"}
    # An older use flushed late never overwrites a newer one.
    store.put("tenant_usage", {**store.get("tenant_usage").data, "ci": {"last_used_at": "2999-01-01T00:00:00+00:00"}})
    a.touch("ci", "10.0.0.9")
    a.flush()
    assert a.read()["ci"]["last_used_at"].startswith("2999")
