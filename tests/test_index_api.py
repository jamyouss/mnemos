from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from qdrant_client import QdrantClient

from core.indexer import Indexer
from server.auth import hash_key
from tests.config_fakes import InMemoryConfigQdrant, boot, doc_store, isolate_settings

ROOT = {"Authorization": "Bearer root-key"}
DEV = {"Authorization": "Bearer dev-key"}


@pytest.fixture
def client(tmp_path, monkeypatch):
    backend = InMemoryConfigQdrant()
    doc_store(backend).put("tenants", {"tenants": {
        "root": {"key_sha256": hash_key("root-key"), "admin": True, "read": "*", "write": "*"},
        "dev": {"key_sha256": hash_key("dev-key"), "admin": False, "read": ["acme"], "write": ["acme"]},
    }})
    codebase = tmp_path / "codebase"
    (codebase / "acme" / "web").mkdir(parents=True)
    (codebase / "acme" / "web" / "app.ts").write_text("export const a = 1\n")
    isolate_settings(monkeypatch, tmp_path, mnemos_auth_enabled=True, qdrant_api_key="qk",
                     codebase_path=str(codebase))
    monkeypatch.setattr("server.api._ALLOWED_BASE_DIRS", [str(codebase)])
    with boot(monkeypatch, backend) as client:
        embeddings = MagicMock(dimension=4)
        embeddings.embed_documents.side_effect = lambda texts, titles=None: [[0.1, 0.2, 0.3, 0.4]] * len(texts)
        qdrant = QdrantClient(":memory:")
        indexer = Indexer(qdrant_client=qdrant, embedding_service=embeddings, codebase_root=str(codebase),
                          path_tags={"acme/": ["acme"], "other/": ["other"]},
                          path_excludes={"acme/": {"exts": [".csv"], "dirs": []}})
        indexer.ensure_collection("mnemos_code")
        root = str(codebase)
        indexer.index_file("export const a = 1\n", f"{root}/acme/web/app.ts", "mnemos_code")
        indexer.index_file("# Notes\n\n## One\ntext\n", f"{root}/acme/README.md", "mnemos_code")
        indexer.index_file("package x\n", f"{root}/other/x.go", "mnemos_code")
        client.app.state.qdrant = qdrant
        client.app.state.indexer = indexer
        client.root = root
        yield client


def test_browse_lists_one_entry_per_file_within_the_read_scope(client):
    every = client.get("/api/index/browse", params={"collection": "mnemos_code"}, headers=ROOT).json()
    assert every["total"] == 3 and len({i["file_path"] for i in every["items"]}) == 3

    scoped = client.get("/api/index/browse", params={"collection": "mnemos_code"}, headers=DEV).json()
    assert sorted(i["file_path"].rsplit("/", 1)[1] for i in scoped["items"]) == ["README.md", "app.ts"]

    under = client.get("/api/index/browse", headers=ROOT,
                       params={"collection": "mnemos_code", "directory": f"{client.root}/acme/web"}).json()
    assert [i["file_path"] for i in under["items"]] == [f"{client.root}/acme/web/app.ts"]


def test_a_file_shows_its_chunks_and_what_the_caller_may_do(client):
    path = f"{client.root}/acme/web/app.ts"
    body = client.get("/api/index/file", params={"collection": "mnemos_code", "file_path": path}, headers=ROOT).json()
    assert body["tags"] == ["acme"] and body["language"] == "typescript"
    assert body["chunks"][0]["chunk_index"] == 0 and "export const a" in body["chunks"][0]["content"]
    assert body["reindexable"] is True and body["deletable"] is True

    # The file exists on disk but a scoped key may not walk the mount.
    dev = client.get("/api/index/file", params={"collection": "mnemos_code", "file_path": path}, headers=DEV).json()
    assert dev["reindexable"] is False and dev["deletable"] is True
    # Pushed or vanished content cannot be read again by a reindex.
    readme = client.get("/api/index/file", headers=ROOT,
                        params={"collection": "mnemos_code", "file_path": f"{client.root}/acme/README.md"}).json()
    assert readme["reindexable"] is False
    hidden = client.get("/api/index/file", headers=DEV,
                        params={"collection": "mnemos_code", "file_path": f"{client.root}/other/x.go"})
    assert hidden.status_code == 404


@pytest.mark.parametrize("path, expected", [
    ("acme/web/app.ts", {"indexed": True, "skipped": False, "rule": None, "tags": ["acme"],
                         "project_prefix": "acme/", "chunker": "fallback", "language": "typescript"}),
    ("acme/node_modules/x/index.js", {"skipped": True, "rule": "built-in"}),
    ("acme/data/export.csv", {"skipped": True, "rule": "project"}),
    ("acme/api/main.go", {"indexed": False, "skipped": False, "chunker": "go"}),
])
def test_check_path_says_why(client, path, expected):
    body = client.post("/api/index/check-path", json={"path": path}, headers=ROOT).json()
    assert body["collection"] == "mnemos_code"
    assert {k: body[k] for k in expected} == expected
    if expected.get("rule"):
        assert body["reason"]


def test_check_path_under_allowlist_mode_and_for_scoped_keys(client):
    client.app.state.indexer._only_declared_paths = True
    body = client.post("/api/index/check-path", json={"path": "loose/x.py"}, headers=ROOT).json()
    assert body["rule"] == "declared-only" and body["project_prefix"] is None
    assert client.post("/api/index/check-path", json={"path": "acme/x.py"}, headers=DEV).status_code == 403
