from __future__ import annotations

import json
import logging

import pytest

from server.auth import TenantsConfigError, hash_key
from server.config import Settings
from server.config_bootstrap import bootstrap_config
from tests.config_fakes import InMemoryConfigQdrant, doc_store

CONNECTORS = """
interval: 6h
sources:
  - type: jira
    name: tracker
    url: https://jira.example.com
    token_env: TRACKER_TOKEN
    token: secret-pat-4242
    projects: [OPS]
"""

PROJECTS = """
paths:
  Projects/acme/:
    - acme
  Projects/acme/reports/:
    tags: [acme-reports]
    exclude_dirs: [exports]
"""

TENANTS = """
tenants:
  ci:
    api_key: "key-ci"
  alice:
    api_key: "key-alice"
"""


@pytest.fixture
def files(tmp_path):
    state = tmp_path / "state"
    state.mkdir()
    paths = {
        "connectors": tmp_path / "connectors.yaml",
        "projects": tmp_path / "projects.yaml",
        "settings": state / "settings.json",
        "tenants": tmp_path / "tenants.yaml",
    }
    return paths


def _settings(files, **kw) -> Settings:
    return Settings(
        mnemos_connectors_config=str(files["connectors"]),
        mnemos_projects_config_path=str(files["projects"]),
        mnemos_state_dir=str(files["settings"].parent),
        mnemos_tenants_path=str(files["tenants"]),
        **kw,
    )


def _write_all(files):
    files["connectors"].write_text(CONNECTORS)
    files["projects"].write_text(PROJECTS)
    files["settings"].write_text(json.dumps({"mnemos_cache_enabled": True, "qdrant_host": "evil"}))
    files["tenants"].write_text(TENANTS)


def test_every_file_is_imported_once(files):
    _write_all(files)
    backend = InMemoryConfigQdrant()
    store = doc_store(backend)
    bootstrap_config(store, _settings(files))

    assert store.revisions() == {"connectors": 1, "projects": 1, "settings": 1, "tenants": 1}
    connectors = store.get("connectors").data
    assert connectors["interval"] == "6h"
    # Secrets never reach the database, even pasted into the file.
    assert "token" not in connectors["sources"][0]
    assert "secret-pat-4242" not in json.dumps(backend.points)
    assert store.get("projects").data["paths"]["Projects/acme/reports/"]["exclude_dirs"] == ["exports"]
    # Settings are cleaned like the overrides file always was.
    assert store.get("settings").data == {"mnemos_cache_enabled": True}

    tenants = store.get("tenants").data["tenants"]
    assert set(tenants) == {"ci", "alice"}
    # The power every key had before scopes existed.
    assert tenants["ci"]["key_sha256"] == hash_key("key-ci")
    assert (tenants["ci"]["admin"], tenants["ci"]["read"], tenants["ci"]["write"]) == (True, "*", "*")
    assert "key-ci" not in json.dumps(backend.points)


def test_a_second_start_does_not_reimport(files):
    _write_all(files)
    backend = InMemoryConfigQdrant()
    store = doc_store(backend)
    bootstrap_config(store, _settings(files))
    store.put("projects", {"paths": {}})
    upserts = backend.upserts

    files["projects"].write_text("paths:\n  other/: [other]\n")
    bootstrap_config(store, _settings(files))

    assert backend.upserts == upserts
    assert store.get("projects").data == {"paths": {}}


def test_absent_files_leave_their_keys_absent(files):
    store = doc_store()
    bootstrap_config(store, _settings(files))
    assert store.revisions() == {}

    # Supplied later, a file is still imported on the next start.
    files["projects"].write_text(PROJECTS)
    bootstrap_config(store, _settings(files))
    assert store.revisions() == {"projects": 1}


def test_bootstrap_admin_key_creates_tenant_admin(files):
    store = doc_store()
    bootstrap_config(store, _settings(files, mnemos_bootstrap_admin_key="boot-key"))
    tenants = store.get("tenants").data["tenants"]
    assert set(tenants) == {"admin"}
    assert tenants["admin"]["key_sha256"] == hash_key("boot-key")
    assert tenants["admin"]["admin"] is True


def test_tenants_file_wins_over_the_bootstrap_key(files):
    files["tenants"].write_text(TENANTS)
    store = doc_store()
    bootstrap_config(store, _settings(files, mnemos_bootstrap_admin_key="boot-key"))
    assert set(store.get("tenants").data["tenants"]) == {"ci", "alice"}


def test_auth_without_any_tenant_refuses_to_start(files):
    with pytest.raises(TenantsConfigError, match="no tenant"):
        bootstrap_config(doc_store(), _settings(files, mnemos_auth_enabled=True))


def test_auth_with_tenants_already_stored_ignores_the_files(files):
    store = doc_store()
    store.put("tenants", {"tenants": {"x": {"key_sha256": hash_key("k"), "admin": True,
                                             "read": "*", "write": "*"}}})
    bootstrap_config(store, _settings(files, mnemos_auth_enabled=True))
    assert store.revisions() == {"tenants": 1}


def test_broken_tenants_file_stops_an_authenticated_start(files):
    files["tenants"].write_text('tenants:\n  a:\n    api_key: "CHANGE_ME"\n')
    with pytest.raises(TenantsConfigError, match="usable api_key"):
        bootstrap_config(doc_store(), _settings(files, mnemos_auth_enabled=True))


def test_broken_tenants_file_is_skipped_without_auth(files, caplog):
    files["tenants"].write_text("tenants: {}\n")
    store = doc_store()
    with caplog.at_level(logging.WARNING, logger="mnemos.config_bootstrap"):
        bootstrap_config(store, _settings(files))
    assert store.get("tenants") is None
    assert str(files["tenants"]) in caplog.text


def test_corrupt_settings_file_is_not_imported(files, caplog):
    files["settings"].write_text("{not json")
    store = doc_store()
    with caplog.at_level(logging.WARNING, logger="mnemos.config_bootstrap"):
        bootstrap_config(store, _settings(files))
    assert store.get("settings") is None
    assert "invalid JSON" in caplog.text
