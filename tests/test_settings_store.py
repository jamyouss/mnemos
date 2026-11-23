from __future__ import annotations

import json
import logging
from types import SimpleNamespace

import pytest

from core.config_store import ConfigDocStore, RevisionConflict
from server.config import Settings
from server.settings_store import ConfigStore, ConfigValidationError
from tests.config_fakes import InMemoryConfigQdrant, doc_store


def _field(snapshot: dict, key: str) -> dict:
    for section in snapshot["sections"]:
        for field in section["fields"]:
            if field["key"] == key:
                return field
    raise KeyError(key)


@pytest.fixture
def clean_env(monkeypatch):
    for name in ("MNEMOS_CACHE_THRESHOLD", "MNEMOS_CACHE_TTL_SECONDS", "MNEMOS_MMR_LAMBDA",
                 "MNEMOS_CACHE_ENABLED", "MNEMOS_LLM_MODEL", "MNEMOS_LLM_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    return monkeypatch


def _store(docs: ConfigDocStore | None = None, overrides: dict | None = None,
           ) -> tuple[Settings, ConfigStore, ConfigDocStore]:
    docs = docs or doc_store()
    if overrides is not None:
        docs.put("settings", overrides)
    settings = Settings()
    return settings, ConfigStore(settings, docs), docs


def test_precedence_override_then_env_then_default(clean_env):
    clean_env.setenv("MNEMOS_CACHE_THRESHOLD", "0.9")
    clean_env.setenv("MNEMOS_CACHE_TTL_SECONDS", "60")
    settings, store, _ = _store(overrides={"mnemos_cache_threshold": 0.8})

    snap = store.snapshot()
    threshold = _field(snap, "mnemos_cache_threshold")
    assert (threshold["value"], threshold["source"]) == (0.8, "override")
    assert settings.mnemos_cache_threshold == 0.8  # applied at startup
    ttl = _field(snap, "mnemos_cache_ttl_seconds")
    assert (ttl["value"], ttl["source"]) == (60, "env")
    mmr = _field(snap, "mnemos_mmr_lambda")
    assert (mmr["value"], mmr["source"], mmr["default"]) == (0.5, "default", 0.5)


def test_env_source_is_case_insensitive(clean_env):
    clean_env.setenv("mnemos_mmr_lambda", "0.3")
    _, store, _ = _store()
    field = _field(store.snapshot(), "mnemos_mmr_lambda")
    assert (field["value"], field["source"]) == (0.3, "env")


def test_snapshot_carries_the_revision(clean_env):
    _, store, _ = _store()
    assert store.snapshot()["revision"] == 0
    store.update({"mnemos_cache_enabled": True}, SimpleNamespace())
    snap = store.snapshot()
    assert snap["revision"] == 1
    assert "overrides_path" not in snap


def test_update_persists_and_reloads_at_startup(clean_env):
    _, store, docs = _store()
    store.update({"mnemos_cache_enabled": True, "mnemos_llm_model": "qwen2.5:7b"}, SimpleNamespace())

    assert docs.get("settings").data == {"mnemos_cache_enabled": True, "mnemos_llm_model": "qwen2.5:7b"}

    settings, reloaded, _ = _store(docs)
    assert settings.mnemos_cache_enabled is True
    assert settings.mnemos_llm_model == "qwen2.5:7b"
    # Loaded at boot: the restart key is no longer pending.
    assert reloaded.pending_restart() == []


def test_failed_write_leaves_the_document_and_memory_untouched(clean_env):
    class Broken(InMemoryConfigQdrant):
        broken = False

        def upsert(self, *args, **kwargs):
            if self.broken:
                raise ConnectionError("qdrant down")
            super().upsert(*args, **kwargs)

    backend = Broken()
    settings, store, docs = _store(doc_store(backend), {"mnemos_cache_enabled": False})
    backend.broken = True
    with pytest.raises(ConnectionError):
        store.update({"mnemos_cache_enabled": True}, SimpleNamespace())

    assert docs.get("settings").data == {"mnemos_cache_enabled": False}
    assert _field(store.snapshot(), "mnemos_cache_enabled")["value"] is False
    assert settings.mnemos_cache_enabled is False


def test_invalid_document_entries_are_ignored_with_a_warning(clean_env, caplog):
    with caplog.at_level(logging.WARNING, logger="mnemos.settings"):
        settings, store, _ = _store(overrides={
            "nope": 1,
            "qdrant_host": "evil",
            "mnemos_cache_threshold": 7,     # out of range
            "mnemos_mmr_lambda": 0.2,
        })
    assert settings.qdrant_host == "localhost"
    assert settings.mnemos_cache_threshold == 0.95
    assert settings.mnemos_mmr_lambda == 0.2
    assert "nope" in caplog.text and "qdrant_host" in caplog.text
    assert _field(store.snapshot(), "qdrant_host")["source"] != "override"


def test_validation_is_all_or_nothing(clean_env):
    settings, store, docs = _store()
    with pytest.raises(ConfigValidationError) as exc:
        store.update({
            "mnemos_cache_enabled": True,
            "mnemos_grader_workers": 99,
            "qdrant_host": "x",
            "bogus": 1,
        }, SimpleNamespace())
    assert set(exc.value.errors) == {"mnemos_grader_workers", "qdrant_host", "bogus"}
    assert docs.get("settings") is None
    assert settings.mnemos_cache_enabled is False


def test_live_keys_apply_to_settings_and_component(clean_env):
    settings, store, _ = _store()
    cache = SimpleNamespace(enabled=False, threshold=0.95)
    applied = store.update({"mnemos_cache_enabled": True, "mnemos_cache_threshold": 0.9},
                           SimpleNamespace(cache=cache))
    assert applied == ["mnemos_cache_enabled", "mnemos_cache_threshold"]
    assert (cache.enabled, cache.threshold) == (True, 0.9)
    assert settings.mnemos_cache_enabled is True


def test_restart_keys_wait_for_a_restart(clean_env):
    settings, store, _ = _store()
    applied = store.update({"mnemos_llm_model": "qwen2.5:7b"}, SimpleNamespace())
    assert applied == []
    assert store.pending_restart() == ["mnemos_llm_model"]
    assert settings.mnemos_llm_model == "llama3.1:8b"  # nothing live
    assert _field(store.snapshot(), "mnemos_llm_model")["value"] == "qwen2.5:7b"

    # Saving the boot value back is not a pending change.
    store.update({"mnemos_llm_model": "llama3.1:8b"}, SimpleNamespace())
    assert store.pending_restart() == []


def test_reset_reverts_to_env_and_reapplies_live(clean_env):
    clean_env.setenv("MNEMOS_CACHE_THRESHOLD", "0.9")
    settings, store, docs = _store(overrides={"mnemos_cache_threshold": 0.5})
    cache = SimpleNamespace(threshold=0.5)

    store.reset("mnemos_cache_threshold", SimpleNamespace(cache=cache))

    assert settings.mnemos_cache_threshold == 0.9
    assert cache.threshold == 0.9
    assert docs.get("settings").data == {}
    field = _field(store.snapshot(), "mnemos_cache_threshold")
    assert (field["value"], field["source"]) == (0.9, "env")
    with pytest.raises(KeyError):
        store.reset("mnemos_cache_threshold", SimpleNamespace())


def test_stale_revision_is_refused_and_saves_nothing(clean_env):
    _, store, docs = _store(overrides={"mnemos_cache_enabled": True})
    with pytest.raises(RevisionConflict) as exc:
        store.update({"mnemos_mmr_lambda": 0.2}, SimpleNamespace(), revision=0)
    assert exc.value.current == 1
    with pytest.raises(RevisionConflict):
        store.reset("mnemos_cache_enabled", SimpleNamespace(), revision=7)
    assert docs.get("settings").data == {"mnemos_cache_enabled": True}

    store.update({"mnemos_mmr_lambda": 0.2}, SimpleNamespace(), revision=1)
    assert docs.get("settings").revision == 2


def test_update_without_revision_keeps_another_pods_save(clean_env):
    docs = doc_store()
    _, pod_a, _ = _store(docs)
    _, pod_b, _ = _store(docs)
    pod_a.update({"mnemos_cache_enabled": True}, SimpleNamespace())
    # pod_b has not polled yet: its save still merges onto the stored document.
    pod_b.update({"mnemos_mmr_lambda": 0.2}, SimpleNamespace())
    assert docs.get("settings").data == {"mnemos_cache_enabled": True, "mnemos_mmr_lambda": 0.2}


def test_apply_doc_reaches_components_and_reports_pending_restart(clean_env):
    docs = doc_store()
    settings, store, _ = _store(docs)
    cache = SimpleNamespace(enabled=False)
    state = SimpleNamespace(cache=cache)

    doc = docs.put("settings", {"mnemos_cache_enabled": True, "mnemos_llm_model": "phi3"})
    assert store.apply_doc(doc, state) == ["mnemos_cache_enabled"]
    assert cache.enabled is True and settings.mnemos_cache_enabled is True
    assert store.pending_restart() == ["mnemos_llm_model"]
    assert store.revision == 1

    # Same or older revision: nothing to do.
    cache.enabled = False
    assert store.apply_doc(doc, state) == []
    assert cache.enabled is False

    # An override removed elsewhere reverts the live value.
    cache.enabled = True
    assert store.apply_doc(docs.put("settings", {}), state) == ["mnemos_cache_enabled"]
    assert cache.enabled is False
    assert store.pending_restart() == []


def test_secret_value_never_in_snapshot(clean_env):
    clean_env.setenv("MNEMOS_LLM_API_KEY", "sk-SENTINEL-123")
    _, store, _ = _store()
    snap = store.snapshot()
    key = _field(snap, "mnemos_llm_api_key")
    assert key["value"] is None and key["default"] is None
    assert key["is_set"] is True
    assert "SENTINEL" not in json.dumps(snap)
