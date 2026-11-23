from __future__ import annotations

import pytest

from server.auth import hash_key
from tests.config_fakes import InMemoryConfigQdrant, boot, doc_store, isolate_settings


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


ROWS = [
    {"prefix": "Projects/acme/", "tags": ["acme", "acme-corp"]},
    {"prefix": "Projects/acme/reports/", "tags": ["acme-reports"],
     "exclude_dirs": ["exports"], "exclude_exts": [".csv"]},
]


def test_absent_document_is_empty(make_client):
    assert make_client().get("/api/projects").json() == {
        "revision": 0, "writable": True, "paths": [], "problems": [],
    }


def test_put_stores_both_entry_forms_and_retags_the_indexer(make_client, backend):
    client = make_client()
    resp = client.put("/api/projects", json={"paths": ROWS})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["revision"] == 1 and body["problems"] == []
    assert body["paths"] == [
        {"prefix": "Projects/acme/", "tags": ["acme", "acme-corp"],
         "exclude_dirs": [], "exclude_exts": []},
        {"prefix": "Projects/acme/reports/", "tags": ["acme-reports"],
         "exclude_dirs": ["exports"], "exclude_exts": [".csv"]},
    ]
    # Same shape as projects.yaml: shorthand unless a row has ignore rules.
    assert doc_store(backend).get("projects").data == {"paths": {
        "Projects/acme/": ["acme", "acme-corp"],
        "Projects/acme/reports/": {"tags": ["acme-reports"], "exclude_dirs": ["exports"],
                                   "exclude_exts": [".csv"]},
    }}

    indexer = client.app.state.indexer
    assert indexer.resolve_tags("/data/codebase/Projects/acme/app/x.go", None) == ["acme", "acme-corp"]
    assert indexer.should_skip("/data/codebase/Projects/acme/reports/q1.csv") is True
    assert client.app.state.path_tags["Projects/acme/"] == ["acme", "acme-corp"]


def test_stored_projects_apply_at_startup(make_client, backend):
    doc_store(backend).put("projects", {"paths": {"acme/": ["acme"]}})
    client = make_client()
    assert client.app.state.indexer.resolve_tags("/data/codebase/acme/x/y.go", None) == ["acme"]
    assert client.get("/api/projects").json()["paths"][0]["prefix"] == "acme/"


def test_invalid_rows_are_refused_with_per_row_problems(make_client, backend):
    doc_store(backend).put("projects", {"paths": {"keep/": ["keep"]}})
    resp = make_client().put("/api/projects", json={"paths": [
        {"prefix": "  ", "tags": ["x"]},
        {"prefix": "/abs/", "tags": ["x"]},
        {"prefix": "dup/", "tags": ["a"]},
        {"prefix": "dup", "tags": ["b"]},
        {"prefix": "notags/", "tags": [" "]},
        {"prefix": "badtype/", "tags": "acme"},
        "not-an-object",
    ]})
    assert resp.status_code == 422
    problems = resp.json()["problems"]
    assert "paths[0]: empty prefix" in problems
    assert any(p.startswith("paths[1]: ") and "leading /" in p for p in problems)
    assert any(p.startswith("paths[2]: duplicate prefix") for p in problems)
    assert any(p.startswith("paths[3]: duplicate prefix") for p in problems)
    assert "paths[4]: no tags" in problems
    assert "paths[5]: tags must be a list of strings" in problems
    assert "paths[6]: expected an object" in problems
    assert doc_store(backend).get("projects").data == {"paths": {"keep/": ["keep"]}}


def test_put_is_forbidden_when_not_writable(make_client, backend):
    resp = make_client(writable=False).put("/api/projects", json={"paths": ROWS})
    assert resp.status_code == 403
    assert doc_store(backend).get("projects") is None


def test_stale_revision_is_a_conflict(make_client):
    client = make_client()
    client.put("/api/projects", json={"paths": ROWS})
    resp = client.put("/api/projects", json={"paths": ROWS[:1], "revision": 0})
    assert resp.status_code == 409
    assert resp.json()["revision"] == 1
    assert len(client.get("/api/projects").json()["paths"]) == 2
    assert client.put("/api/projects", json={"paths": ROWS[:1], "revision": 1}).status_code == 200


def test_scripts_key_reads_but_only_admins_write(make_client, backend):
    def entry(key, admin, write):
        return {"key_sha256": hash_key(key), "admin": admin, "read": "*", "write": write}

    doc_store(backend).put("tenants", {"tenants": {
        "root": entry("root", True, "*"),
        "scripts": entry("scripts", False, "*"),
        "dev": entry("dev", False, ["acme"]),
    }})
    client = make_client(mnemos_auth_enabled=True, qdrant_api_key="qk")

    def as_(key):
        return {"Authorization": f"Bearer {key}"}

    assert client.get("/api/projects", headers=as_("scripts")).status_code == 200
    assert client.get("/api/projects", headers=as_("dev")).status_code == 403
    assert client.put("/api/projects", headers=as_("scripts"), json={"paths": ROWS}).status_code == 403
    assert client.put("/api/projects", headers=as_("root"), json={"paths": ROWS}).status_code == 200
