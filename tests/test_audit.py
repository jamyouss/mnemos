from __future__ import annotations

import pytest
from qdrant_client import QdrantClient

from core.event_log import EventLog
from server.audit import AUDIT_FIELDS
from server.auth import hash_key
from tests.config_fakes import InMemoryConfigQdrant, boot, doc_store, isolate_settings

ROOT = {"Authorization": "Bearer root-key"}
DEV = {"Authorization": "Bearer dev-key"}


def _tenant(key: str, admin: bool, write) -> dict:
    return {"key_sha256": hash_key(key), "admin": admin, "read": "*", "write": write}


@pytest.fixture(autouse=True)
def _fresh_denials(monkeypatch):
    # Refusals are deduplicated per process for a minute; tests start clean.
    monkeypatch.setattr("server.audit._denials", {})
    monkeypatch.setattr("server.audit._failures", {"count": 0, "last_at": None})


@pytest.fixture
def client(tmp_path, monkeypatch):
    backend = InMemoryConfigQdrant()
    doc_store(backend).put("tenants", {"tenants": {
        "root": _tenant("root-key", True, "*"),
        "dev": _tenant("dev-key", False, ["acme"]),
    }})
    isolate_settings(monkeypatch, tmp_path, mnemos_auth_enabled=True, qdrant_api_key="qk")
    with boot(monkeypatch, backend) as client:
        log = EventLog(QdrantClient(":memory:"), "audit", AUDIT_FIELDS, retention_days=365)
        log.ensure_collection()
        client.app.state.audit = log
        yield client


def _entries(client) -> list[dict]:
    return client.get("/api/audit", headers=ROOT).json()["items"]


def test_tenant_changes_are_recorded_without_their_keys(client):
    created = client.post("/api/tenants", headers=ROOT, json={"name": "ci", "read": "*", "write": ["acme"]}).json()
    client.patch("/api/tenants/ci", headers=ROOT, json={"write": "*"})
    client.post("/api/tenants/ci/rotate", headers=ROOT)
    client.delete("/api/tenants/ci", headers=ROOT)

    entries = _entries(client)
    assert [e["action"] for e in entries] == ["tenant.delete", "tenant.rotate", "tenant.update", "tenant.create"]
    assert {e["actor"] for e in entries} == {"root"} and {e["target"] for e in entries} == {"ci"}
    assert entries[2]["changes"] == {"write": [["acme"], "*"]}
    # Neither the key handed out nor its hash ever reaches the trail.
    assert created["api_key"] not in str(entries) and "key_sha256" not in str(entries)


def test_a_refused_change_is_recorded_as_denied(client):
    assert client.put("/api/projects", headers=DEV, json={"paths": []}).status_code == 403
    assert client.delete("/api/tenants/root", headers=DEV).status_code == 403

    entries = _entries(client)
    assert [(e["action"], e["actor"], e["outcome"]) for e in entries] == [
        ("tenant.delete", "dev", "denied"), ("projects.update", "dev", "denied"),
    ]
    assert entries[0]["target"] == "root" and entries[0]["detail"]


def test_settings_changes_record_old_and_new_values_and_mask_secrets(client):
    from server.config_api import _shown

    client.patch("/api/config", headers=ROOT, json={"values": {"mnemos_cache_ttl_seconds": 60}})

    assert _entries(client)[0]["changes"] == {"mnemos_cache_ttl_seconds": [3600, 60]}
    # No secret is editable today; one that becomes editable is masked, set or not.
    assert _shown("mnemos_llm_api_key", ["", "sk-secret"]) == ["", "••••"]


def test_project_changes_list_added_removed_and_changed_prefixes(client):
    client.put("/api/projects", headers=ROOT, json={"paths": [
        {"prefix": "acme/", "tags": ["acme"]}, {"prefix": "old/", "tags": ["old"]},
    ]})
    client.put("/api/projects", headers=ROOT, json={"paths": [
        {"prefix": "acme/", "tags": ["acme", "web"]}, {"prefix": "new/", "tags": ["new"]},
    ]})

    changes = _entries(client)[0]["changes"]
    assert changes["added"] == ["new/"] and changes["removed"] == ["old/"]
    assert changes["changed"] == {"acme/": {"tags": [["acme"], ["acme", "web"]]}}


def test_the_trail_is_for_admins_and_filters(client):
    client.post("/api/tenants", headers=ROOT, json={"name": "ci"})
    client.put("/api/projects", headers=DEV, json={"paths": []})

    assert client.get("/api/audit", headers=DEV).status_code == 403
    body = client.get("/api/audit", headers=ROOT, params={"outcome": "denied"}).json()
    assert [e["action"] for e in body["items"]] == ["projects.update"]
    assert [e["action"] for e in client.get("/api/audit", headers=ROOT, params={"domain": "tenant"}).json()["items"]] \
        == ["tenant.create"]
    assert body["retention_days"] == 365


def test_mcp_writes_are_recorded_with_their_channel(client):
    import asyncio
    from unittest.mock import MagicMock

    from server.mcp_tools import _dispatch_tool
    from server.principal import Principal

    dev = Principal(name="dev", admin=False, read=None, write=frozenset({"acme"}))
    with pytest.raises(Exception):
        asyncio.run(_dispatch_tool("mnemos_reindex", {"collection": "mnemos_code"}, dev, MagicMock(), MagicMock(),
                                   None, audit_log=client.app.state.audit))
    entry = _entries(client)[0]
    assert (entry["action"], entry["actor"], entry["outcome"], entry["via"]) == ("index.reindex", "dev", "denied", "mcp")


def test_a_loop_of_refusals_is_recorded_once(client):
    for _ in range(5):
        assert client.put("/api/projects", headers=DEV, json={"paths": []}).status_code == 403
    assert [e["action"] for e in _entries(client)] == ["projects.update"]


def test_an_unknown_key_is_recorded_but_no_key_at_all_is_not(client):
    assert client.get("/api/whoami").status_code == 401  # the dashboard before sign-in
    assert client.get("/api/whoami", headers={"Authorization": "Bearer guessed"}).status_code == 401

    entries = _entries(client)
    assert [(e["action"], e["actor"], e["detail"], e["target"]) for e in entries] == [
        ("auth.failed", "anonymous", "unknown key", "/api/whoami"),
    ]
    assert "guessed" not in str(entries)


def test_credentials_in_urls_never_reach_the_trail(client):
    from server.audit import redact

    source = {"type": "git", "name": "repo", "url": "https://bot:ghp_old@github.com/acme/app.git"}
    client.put("/api/connectors", headers=ROOT, json={"sources": [source]})
    client.put("/api/connectors", headers=ROOT, json={"sources": [{**source, "url": source["url"].replace("old", "new")}]})

    entry = _entries(client)[0]
    assert entry["changes"]["changed"]["repo"]["url"] == [
        "https://••••@github.com/acme/app.git", "https://••••@github.com/acme/app.git",
    ]
    assert "ghp_" not in str(_entries(client))
    assert redact("https://h/x?ref=main&token=abc&page=2") == "https://h/x?ref=main&token=••••&page=2"


def test_audit_and_index_history_cannot_be_deleted_through_the_index_api(client):
    for collection in ("mnemos_audit", "mnemos_index_events"):
        assert client.delete(f"/api/index/{collection}/any/file.md", headers=ROOT).status_code == 404


def test_entries_that_could_not_be_stored_are_counted(client):
    from unittest.mock import MagicMock

    working = client.app.state.audit
    broken = MagicMock(retention_days=365)
    broken.record.side_effect = RuntimeError("Qdrant down")
    client.app.state.audit = broken
    client.post("/api/tenants", headers=ROOT, json={"name": "ci"})
    client.app.state.audit = working

    body = client.get("/api/audit", headers=ROOT).json()
    assert body["write_failures"] == 1 and body["last_write_failure_at"]
