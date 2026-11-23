from __future__ import annotations

import pytest

from server.auth import hash_key
from tests.config_fakes import InMemoryConfigQdrant, boot, doc_store, isolate_settings

ROOT = {"Authorization": "Bearer root-key"}


def _admin(key: str) -> dict:
    return {"key_sha256": hash_key(key), "admin": True, "read": "*", "write": "*",
            "created_at": "2026-01-01T00:00:00+00:00"}


@pytest.fixture
def backend():
    backend = InMemoryConfigQdrant()
    doc_store(backend).put("tenants", {"tenants": {"root": _admin("root-key")}})
    return backend


@pytest.fixture
def make_client(tmp_path, monkeypatch, backend):
    clients = []

    def build(writable: bool = True):
        isolate_settings(monkeypatch, tmp_path, mnemos_ui_config_writable=writable,
                         mnemos_auth_enabled=True, qdrant_api_key="qk")
        client = boot(monkeypatch, backend).__enter__()
        client.headers.update(ROOT)
        clients.append(client)
        return client

    yield build
    for client in clients:
        client.__exit__(None, None, None)


def _stored(backend) -> dict:
    return doc_store(backend).get("tenants").data["tenants"]


def test_list_never_shows_the_hash(make_client):
    resp = make_client().get("/api/tenants")
    assert resp.status_code == 200
    assert resp.json() == {"revision": 1, "writable": True, "tenants": [{
        "name": "root", "admin": True, "root": False, "read": "*", "write": "*",
        "created_at": "2026-01-01T00:00:00+00:00",
    }]}
    assert hash_key("root-key") not in resp.text


def test_create_returns_the_key_once_and_stores_its_hash(make_client, backend):
    client = make_client()
    resp = client.post("/api/tenants", json={
        "name": "dev", "admin": False, "read": ["acme", " web "], "write": ["acme"],
    })
    assert resp.status_code == 200, resp.text
    body = resp.json()
    key = body["api_key"]
    assert len(key) >= 40
    assert body["tenant"]["read"] == ["acme", "web"] and body["revision"] == 2
    stored = _stored(backend)["dev"]
    assert stored["key_sha256"] == hash_key(key)
    assert key not in str(backend.points)
    assert key not in client.get("/api/tenants").text

    me = client.get("/api/whoami", headers={"Authorization": f"Bearer {key}"}).json()
    assert me == {"name": "dev", "admin": False, "read": ["acme", "web"], "write": ["acme"],
                  "auth_enabled": True}


@pytest.mark.parametrize("payload,status", [
    ({"name": "bad name!"}, 422),
    ({"name": "x" * 65}, 422),
    ({"name": "dev", "read": "acme"}, 422),
    ({"name": "dev", "write": [1]}, 422),
    ({"name": "root", "admin": True}, 409),
])
def test_create_refuses_invalid_or_duplicate_tenants(make_client, payload, status):
    resp = make_client().post("/api/tenants", json=payload)
    assert resp.status_code == status
    if status == 409:
        assert "revision" not in resp.json()


def test_patch_changes_only_given_fields(make_client, backend):
    client = make_client()
    client.post("/api/tenants", json={"name": "dev", "read": ["a"], "write": ["a"]})
    resp = client.patch("/api/tenants/dev", json={"write": []})
    assert resp.status_code == 200
    assert resp.json()["tenant"] == {**resp.json()["tenant"], "read": ["a"], "write": []}
    assert _stored(backend)["dev"]["read"] == ["a"]
    assert client.patch("/api/tenants/ghost", json={"admin": True}).status_code == 404


def test_rotate_replaces_the_key(make_client):
    client = make_client()
    old = client.post("/api/tenants", json={"name": "dev", "read": "*"}).json()["api_key"]
    resp = client.post("/api/tenants/dev/rotate")
    assert resp.status_code == 200
    new = resp.json()["api_key"]
    assert new != old
    assert client.get("/api/whoami", headers={"Authorization": f"Bearer {old}"}).status_code == 401
    assert client.get("/api/whoami", headers={"Authorization": f"Bearer {new}"}).status_code == 200


def test_delete_revokes_at_once(make_client, backend):
    client = make_client()
    key = client.post("/api/tenants", json={"name": "dev"}).json()["api_key"]
    resp = client.delete("/api/tenants/dev")
    assert resp.status_code == 200 and resp.json()["deleted"] == "dev"
    assert "dev" not in _stored(backend)
    assert client.get("/api/whoami", headers={"Authorization": f"Bearer {key}"}).status_code == 401
    assert client.delete("/api/tenants/dev").status_code == 404


def test_the_last_admin_cannot_be_removed_or_demoted(make_client, backend):
    client = make_client()
    for resp in (client.delete("/api/tenants/root"),
                 client.patch("/api/tenants/root", json={"admin": False})):
        assert resp.status_code == 409
        assert "revision" not in resp.json()
    assert _stored(backend)["root"]["admin"] is True

    # With a second admin, the first may go.
    client.post("/api/tenants", json={"name": "ops", "admin": True, "read": "*", "write": "*"})
    assert client.patch("/api/tenants/root", json={"admin": False}).status_code == 200


def test_the_root_tenant_cannot_be_edited_rotated_or_deleted(make_client, backend):
    store = doc_store(backend)
    tenants = store.get("tenants").data["tenants"]
    tenants["admin"] = {**_admin("boot-key"), "root": True}
    store.put("tenants", {"tenants": tenants})
    client = make_client()

    for resp in (client.patch("/api/tenants/admin", json={"write": ["acme"]}),
                 client.post("/api/tenants/admin/rotate"),
                 client.delete("/api/tenants/admin")):
        assert resp.status_code == 403
        assert "MNEMOS_BOOTSTRAP_ADMIN_KEY" in resp.json()["detail"]
    assert _stored(backend)["admin"] == {**_admin("boot-key"), "root": True}
    assert client.get("/api/whoami", headers={"Authorization": "Bearer boot-key"}).status_code == 200


def test_stale_revision_is_a_conflict_on_every_mutation(make_client):
    client = make_client()
    client.post("/api/tenants", json={"name": "dev"})  # revision 2
    stale = 1
    for resp in (
        client.post("/api/tenants", json={"name": "x", "revision": stale}),
        client.patch("/api/tenants/dev", json={"read": "*", "revision": stale}),
        client.post("/api/tenants/dev/rotate", json={"revision": stale}),
        client.delete("/api/tenants/dev", params={"revision": stale}),
    ):
        assert resp.status_code == 409
        assert resp.json()["revision"] == 2
    assert client.delete("/api/tenants/dev", params={"revision": 2}).status_code == 200


def test_tenants_are_admin_only(make_client):
    client = make_client()
    key = client.post("/api/tenants", json={"name": "dev", "read": "*", "write": "*"}).json()["api_key"]
    dev = {"Authorization": f"Bearer {key}"}
    assert client.get("/api/tenants", headers=dev).status_code == 403
    assert client.post("/api/tenants", headers=dev, json={"name": "x"}).status_code == 403
    assert client.get("/api/whoami", headers=dev).status_code == 200


def test_writes_are_forbidden_when_not_writable(make_client, backend):
    client = make_client(writable=False)
    assert client.get("/api/tenants").status_code == 200
    assert client.post("/api/tenants", json={"name": "dev"}).status_code == 403
    assert client.delete("/api/tenants/root").status_code == 403
    assert set(_stored(backend)) == {"root"}
