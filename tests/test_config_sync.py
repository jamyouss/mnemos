from __future__ import annotations

import threading

import pytest

from core.config_store import ConfigDoc
from server.auth import hash_key
from server.config_sync import ConfigSync, apply_projects
from tests.config_fakes import InMemoryConfigQdrant, boot, doc_store, isolate_settings


def _doc(key: str, revision: int, data: dict | None = None) -> ConfigDoc:
    return ConfigDoc(key=key, data=data or {}, revision=revision, updated_at="")


# ---------------------------------------------------------------------------
# ConfigSync
# ---------------------------------------------------------------------------


def test_apply_runs_the_applier_once_per_new_revision():
    seen = []
    sync = ConfigSync(doc_store(), 60)
    sync.register("projects", seen.append, revision=1)

    sync.apply(_doc("projects", 1))
    sync.apply(_doc("projects", 2))
    sync.apply(_doc("projects", 2))
    assert [d.revision for d in seen] == [2]
    assert sync.applied_revision("projects") == 2


def test_poll_applies_only_keys_that_moved():
    store = doc_store()
    store.put("projects", {"paths": {}})
    store.put("tenants", {"tenants": {}})
    seen = []
    sync = ConfigSync(store, 60)
    sync.register("projects", seen.append, revision=1)
    sync.register("tenants", seen.append, revision=0)

    sync.poll_once()
    assert [(d.key, d.revision) for d in seen] == [("tenants", 1)]
    sync.poll_once()
    assert len(seen) == 1


def test_a_failing_applier_is_retried_on_the_next_poll():
    store = doc_store()
    store.put("projects", {"paths": {}})
    calls = []

    def flaky(doc):
        calls.append(doc.revision)
        if len(calls) == 1:
            raise RuntimeError("boom")

    sync = ConfigSync(store, 60)
    sync.register("projects", flaky, revision=0)
    with pytest.raises(RuntimeError):
        sync.poll_once()
    assert sync.applied_revision("projects") == 0
    sync.poll_once()
    assert calls == [1, 1] and sync.applied_revision("projects") == 1


def test_keys_without_applier_are_tracked():
    store = doc_store()
    store.put("connectors", {"sources": []})
    sync = ConfigSync(store, 60)
    sync.register("connectors", None, revision=0)
    sync.poll_once()
    assert sync.applied_revision("connectors") == 1


def test_thread_polls_and_stops_cleanly():
    store = doc_store()
    applied = threading.Event()
    sync = ConfigSync(store, 0.01)
    sync.register("projects", lambda doc: applied.set(), revision=0)
    sync.start()
    try:
        store.put("projects", {"paths": {}})
        assert applied.wait(2)
    finally:
        sync.stop()
    assert sync._thread is None


def test_thread_survives_a_failed_poll(caplog):
    class Flaky(InMemoryConfigQdrant):
        failures = 0

        def retrieve(self, *args, **kwargs):
            if self.failures:
                self.failures -= 1
                raise ConnectionError("qdrant down")
            return super().retrieve(*args, **kwargs)

    backend = Flaky()
    store = doc_store(backend)
    store.put("projects", {"paths": {}})
    applied = threading.Event()
    sync = ConfigSync(store, 0.01)
    sync.register("projects", lambda doc: applied.set(), revision=0)
    backend.failures = 1
    sync.start()
    try:
        assert applied.wait(2)
    finally:
        sync.stop()
    assert "Config poll failed" in caplog.text


def test_apply_projects_updates_state_and_indexer():
    class Indexer:
        rules = None

        def set_project_rules(self, tags, excludes):
            self.rules = (tags, excludes)

    class State:
        indexer = Indexer()

    state = State()
    apply_projects(state, {"paths": {"a/": {"tags": ["a"], "exclude_exts": [".csv"]}}})
    assert state.path_tags == {"a/": ["a"]}
    assert state.path_excludes == {"a/": {"exts": [".csv"], "dirs": []}}
    assert state.indexer.rules == (state.path_tags, state.path_excludes)


# ---------------------------------------------------------------------------
# Two pods sharing one database
# ---------------------------------------------------------------------------


def _admin(key: str) -> dict:
    return {"key_sha256": hash_key(key), "admin": True, "read": "*", "write": "*",
            "created_at": ""}


@pytest.fixture
def pods(tmp_path, monkeypatch):
    isolate_settings(monkeypatch, tmp_path, mnemos_auth_enabled=True, qdrant_api_key="qk")
    backend = InMemoryConfigQdrant()
    doc_store(backend).put("tenants", {"tenants": {"root": _admin("root-key")}})
    with boot(monkeypatch, backend) as a, boot(monkeypatch, backend) as b:
        for client in (a, b):
            client.headers["Authorization"] = "Bearer root-key"
        yield a, b


def test_settings_saved_on_one_pod_reach_the_other(pods):
    a, b = pods
    assert b.app.state.cache.enabled is False
    resp = a.patch("/api/config", json={"values": {"mnemos_cache_enabled": True}})
    assert resp.status_code == 200, resp.text
    assert a.app.state.cache.enabled is True

    b.app.state.config_sync.poll_once()
    assert b.app.state.cache.enabled is True
    body = b.get("/api/config").json()
    assert body["revision"] == resp.json()["revision"] == 1


def test_projects_saved_on_one_pod_retag_the_other(pods):
    a, b = pods
    resp = a.put("/api/projects", json={"paths": [{"prefix": "acme/", "tags": ["acme"]}]})
    assert resp.status_code == 200, resp.text
    path = "/data/codebase/acme/app/main.go"
    assert a.app.state.indexer.resolve_tags(path, None) == ["acme"]
    assert b.app.state.indexer.resolve_tags(path, None) == ["acme", "acme/app"]

    b.app.state.config_sync.poll_once()
    assert b.app.state.indexer.resolve_tags(path, None) == ["acme"]
    assert b.app.state.path_tags == {"acme/": ["acme"]}


def test_tenants_saved_on_one_pod_change_who_the_other_lets_in(pods):
    a, b = pods
    created = a.post("/api/tenants", json={"name": "ci", "admin": False, "read": "*", "write": "*"})
    assert created.status_code == 200, created.text
    ci = {"Authorization": f"Bearer {created.json()['api_key']}"}
    assert a.get("/api/whoami", headers=ci).status_code == 200
    assert b.get("/api/whoami", headers=ci).status_code == 401

    b.app.state.config_sync.poll_once()
    assert b.get("/api/whoami", headers=ci).json()["name"] == "ci"

    assert a.delete("/api/tenants/ci").status_code == 200
    assert a.get("/api/whoami", headers=ci).status_code == 401
    assert b.get("/api/whoami", headers=ci).status_code == 200
    b.app.state.config_sync.poll_once()
    assert b.get("/api/whoami", headers=ci).status_code == 401
