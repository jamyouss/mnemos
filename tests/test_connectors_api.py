from __future__ import annotations

import pytest

from connectors.config import parse_config
from server.auth import hash_key
from tests.config_fakes import InMemoryConfigQdrant, boot, doc_store, isolate_settings

JIRA = {
    "type": "jira", "name": "tracker", "url": "https://jira.example.com",
    "token_env": "TRACKER_TOKEN", "projects": ["OPS"],
}


@pytest.fixture
def backend():
    return InMemoryConfigQdrant()


@pytest.fixture
def make_client(tmp_path, monkeypatch, backend):
    clients = []

    def build(writable: bool = True, **extra):
        isolate_settings(monkeypatch, tmp_path, mnemos_ui_config_writable=writable, **extra)
        client = boot(monkeypatch, backend).__enter__()
        clients.append(client)
        return client

    yield build
    for client in clients:
        client.__exit__(None, None, None)


def test_absent_document_is_an_empty_config(make_client):
    body = make_client().get("/api/connectors").json()
    assert body == {"revision": 0, "writable": True, "interval": None, "sources": [],
                    "problems": [], "sync": {"requests": [], "acked_id": 0, "acked_at": None}}


def test_saved_document_is_what_the_ingester_parses(make_client, backend):
    resp = make_client().put("/api/connectors", json={
        "interval": " 30m ",
        "sources": [
            # Empty optional values are dropped, not stored as `user: ''`.
            {**JIRA, "user": "", "jql": None, "tags": []},
            {"type": "web", "urls": ["https://docs.example.com/"], "max_pages": 20},
        ],
    })
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["problems"] == [] and body["revision"] == 1

    stored = doc_store(backend).get("connectors").data
    assert "user" not in stored["sources"][0]
    config = parse_config(stored, environ={"TRACKER_TOKEN": "pat"})
    assert config.interval == 1800
    tracker, docs = config.sources
    assert (tracker.user, tracker.jql, tracker.tags, tracker.token) == (None, None, (), "pat")
    assert docs.max_pages == 20


def test_invalid_put_saves_nothing_and_lists_every_problem(make_client, backend):
    doc_store(backend).put("connectors", {"sources": [JIRA]})
    resp = make_client().put("/api/connectors", json={
        "interval": "soon",
        "sources": [{**JIRA, "projects": []}, {"type": "web", "urls": ["ftp://x"], "token": "s"}],
    })
    assert resp.status_code == 422
    problems = resp.json()["problems"]
    assert any("invalid interval" in p for p in problems)
    assert "sources[0] (tracker): missing 'projects'" in problems
    assert any("unknown key 'token'" in p for p in problems)
    assert doc_store(backend).get("connectors").data == {"sources": [JIRA]}


def test_put_is_forbidden_when_not_writable(make_client, backend):
    resp = make_client(writable=False).put("/api/connectors", json={"sources": [JIRA]})
    assert resp.status_code == 403
    assert doc_store(backend).get("connectors") is None


def test_get_never_echoes_unknown_keys(make_client, backend):
    doc_store(backend).put("connectors", {"sources": [{
        "type": "confluence", "url": "https://wiki.example.com",
        "token_env": "WIKI_TOKEN", "token": "secret-pat-4242", "spaces": ["ENG"],
    }]})
    resp = make_client().get("/api/connectors")
    assert "secret-pat-4242" not in resp.text
    body = resp.json()
    assert body["sources"] == [{
        "type": "confluence", "url": "https://wiki.example.com",
        "token_env": "WIKI_TOKEN", "spaces": ["ENG"],
    }]
    # Validated without the ingester's environment: an unset WIKI_TOKEN is not a problem here.
    assert body["problems"] == ["sources[0] (confluence): unknown key 'token'"]


def test_stale_revision_is_a_conflict(make_client, backend):
    client = make_client()
    assert client.put("/api/connectors", json={"sources": [JIRA]}).json()["revision"] == 1
    resp = client.put("/api/connectors", json={"sources": [JIRA], "interval": "1h", "revision": 0})
    assert resp.status_code == 409
    assert resp.json()["revision"] == 1
    assert "interval" not in doc_store(backend).get("connectors").data

    ok = client.put("/api/connectors", json={"sources": [JIRA], "interval": "1h", "revision": 1})
    assert ok.status_code == 200 and ok.json()["interval"] == "1h"


def test_ingester_key_reads_but_only_admins_write(make_client, backend):
    def entry(key, admin, write):
        return {"key_sha256": hash_key(key), "admin": admin, "read": "*", "write": write}

    doc_store(backend).put("tenants", {"tenants": {
        "root": entry("root", True, "*"),
        "ingester": entry("ingest", False, "*"),
        "dev": entry("dev", False, ["acme"]),
    }})
    client = make_client(mnemos_auth_enabled=True, qdrant_api_key="qk")

    def as_(key):
        return {"Authorization": f"Bearer {key}"}

    assert client.get("/api/connectors", headers=as_("ingest")).status_code == 200
    assert client.get("/api/connectors", headers=as_("dev")).status_code == 403
    assert client.put("/api/connectors", headers=as_("ingest"),
                      json={"sources": [JIRA]}).status_code == 403
    assert client.put("/api/connectors", headers=as_("root"),
                      json={"sources": [JIRA]}).status_code == 200


def test_sync_requests_are_numbered_and_acknowledged(make_client, backend):
    doc_store(backend).put("connectors", {"sources": [JIRA, {"type": "web", "urls": ["https://a.example"]}]})
    client = make_client()

    one = client.post("/api/connectors/sync", json={"sources": ["tracker"]}).json()
    every = client.post("/api/connectors/sync", json={}).json()
    assert one["request"]["id"] == 1 and one["request"]["sources"] == ["tracker"]
    assert every["request"]["id"] == 2 and every["request"]["sources"] is None
    # The ingester reads them with the connectors, then acknowledges the newest.
    assert [r["id"] for r in client.get("/api/connectors").json()["sync"]["requests"]] == [1, 2]
    assert client.post("/api/connectors/sync/ack", json={"id": 2}).json()["acked_id"] == 2
    # A stale acknowledgement never moves it back.
    assert client.post("/api/connectors/sync/ack", json={"id": 1}).json()["acked_id"] == 2
    # An unnamed source answers to its type, as in the ingester.
    assert client.post("/api/connectors/sync", json={"sources": ["web"]}).json()["request"]["id"] == 3


def test_sync_request_for_an_unknown_source_is_refused(make_client, backend):
    doc_store(backend).put("connectors", {"sources": [JIRA]})
    client = make_client()

    resp = client.post("/api/connectors/sync", json={"sources": ["tracker", "ghost"]})
    assert resp.status_code == 422 and "ghost" in resp.json()["detail"]
    assert client.post("/api/connectors/sync", json={"sources": []}).status_code == 422
    assert client.get("/api/connectors/sync").json()["requests"] == []


def test_sync_keeps_only_the_latest_requests(make_client, backend):
    client = make_client()
    for _ in range(25):
        client.post("/api/connectors/sync", json={})
    ids = [r["id"] for r in client.get("/api/connectors/sync").json()["requests"]]
    assert ids == list(range(6, 26))


def test_ingester_key_may_request_and_acknowledge_syncs(make_client, backend):
    doc_store(backend).put("tenants", {"tenants": {
        "root": {"key_sha256": hash_key("root"), "admin": True, "read": "*", "write": "*"},
        "ingester": {"key_sha256": hash_key("ingest"), "admin": False, "read": "*", "write": "*"},
        "dev": {"key_sha256": hash_key("dev"), "admin": False, "read": "*", "write": ["acme"]},
    }})
    client = make_client(mnemos_auth_enabled=True, qdrant_api_key="qk")

    def as_(key):
        return {"Authorization": f"Bearer {key}"}

    assert client.post("/api/connectors/sync", headers=as_("dev"), json={}).status_code == 403
    assert client.post("/api/connectors/sync", headers=as_("root"), json={}).status_code == 200
    assert client.post("/api/connectors/sync/ack", headers=as_("dev"), json={"id": 1}).status_code == 403
    assert client.post("/api/connectors/sync/ack", headers=as_("ingest"), json={"id": 1}).status_code == 200
