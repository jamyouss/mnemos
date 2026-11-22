from __future__ import annotations

import json
import logging
from types import SimpleNamespace

import pytest

from server.config import Settings
from server.settings_store import ConfigStore, ConfigValidationError


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


def _store(tmp_path, overrides: dict | None = None, raw: str | None = None) -> tuple[Settings, ConfigStore]:
    path = tmp_path / "settings.json"
    if raw is not None:
        path.write_text(raw)
    elif overrides is not None:
        path.write_text(json.dumps(overrides))
    settings = Settings(mnemos_state_dir=str(tmp_path))
    return settings, ConfigStore.load(settings)


def test_precedence_override_then_env_then_default(tmp_path, clean_env):
    clean_env.setenv("MNEMOS_CACHE_THRESHOLD", "0.9")
    clean_env.setenv("MNEMOS_CACHE_TTL_SECONDS", "60")
    settings, store = _store(tmp_path, {"mnemos_cache_threshold": 0.8})

    snap = store.snapshot()
    threshold = _field(snap, "mnemos_cache_threshold")
    assert (threshold["value"], threshold["source"]) == (0.8, "override")
    assert settings.mnemos_cache_threshold == 0.8  # applied at startup
    ttl = _field(snap, "mnemos_cache_ttl_seconds")
    assert (ttl["value"], ttl["source"]) == (60, "env")
    mmr = _field(snap, "mnemos_mmr_lambda")
    assert (mmr["value"], mmr["source"], mmr["default"]) == (0.5, "default", 0.5)


def test_env_source_is_case_insensitive(tmp_path, clean_env):
    clean_env.setenv("mnemos_mmr_lambda", "0.3")
    _, store = _store(tmp_path)
    field = _field(store.snapshot(), "mnemos_mmr_lambda")
    assert (field["value"], field["source"]) == (0.3, "env")


def test_update_persists_atomically_and_reloads_at_startup(tmp_path, clean_env):
    _, store = _store(tmp_path)
    store.update({"mnemos_cache_enabled": True, "mnemos_llm_model": "qwen2.5:7b"}, SimpleNamespace())

    path = tmp_path / "settings.json"
    assert json.loads(path.read_text()) == {"mnemos_cache_enabled": True, "mnemos_llm_model": "qwen2.5:7b"}
    assert [p.name for p in tmp_path.iterdir()] == ["settings.json"]  # no temp file left

    settings, reloaded = _store(tmp_path)
    assert settings.mnemos_cache_enabled is True
    assert settings.mnemos_llm_model == "qwen2.5:7b"
    # Loaded at boot: the restart key is no longer pending.
    assert reloaded.pending_restart() == []


def test_failed_write_leaves_the_file_and_memory_untouched(tmp_path, clean_env, monkeypatch):
    _, store = _store(tmp_path, {"mnemos_cache_enabled": False})

    def broken_replace(src, dst):
        raise OSError(28, "No space left on device")

    monkeypatch.setattr("server.settings_store.os.replace", broken_replace)
    with pytest.raises(OSError):
        store.update({"mnemos_cache_enabled": True}, SimpleNamespace())

    assert json.loads((tmp_path / "settings.json").read_text()) == {"mnemos_cache_enabled": False}
    assert [p.name for p in tmp_path.iterdir()] == ["settings.json"]
    assert _field(store.snapshot(), "mnemos_cache_enabled")["value"] is False


def test_corrupt_file_is_ignored_with_a_warning(tmp_path, clean_env, caplog):
    with caplog.at_level(logging.WARNING, logger="mnemos.settings"):
        settings, store = _store(tmp_path, raw="{not json")
    assert str(tmp_path / "settings.json") in caplog.text
    assert settings.mnemos_cache_enabled is False
    assert _field(store.snapshot(), "mnemos_cache_enabled")["source"] == "default"


def test_unknown_and_non_editable_keys_in_file_are_ignored(tmp_path, clean_env, caplog):
    with caplog.at_level(logging.WARNING, logger="mnemos.settings"):
        settings, store = _store(tmp_path, {
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


def test_validation_is_all_or_nothing(tmp_path, clean_env):
    settings, store = _store(tmp_path)
    with pytest.raises(ConfigValidationError) as exc:
        store.update({
            "mnemos_cache_enabled": True,
            "mnemos_grader_workers": 99,
            "qdrant_host": "x",
            "bogus": 1,
        }, SimpleNamespace())
    assert set(exc.value.errors) == {"mnemos_grader_workers", "qdrant_host", "bogus"}
    assert not (tmp_path / "settings.json").exists()
    assert settings.mnemos_cache_enabled is False


def test_live_keys_apply_to_settings_and_component(tmp_path, clean_env):
    settings, store = _store(tmp_path)
    cache = SimpleNamespace(enabled=False, threshold=0.95)
    applied = store.update({"mnemos_cache_enabled": True, "mnemos_cache_threshold": 0.9},
                           SimpleNamespace(cache=cache))
    assert applied == ["mnemos_cache_enabled", "mnemos_cache_threshold"]
    assert (cache.enabled, cache.threshold) == (True, 0.9)
    assert settings.mnemos_cache_enabled is True


def test_restart_keys_wait_for_a_restart(tmp_path, clean_env):
    settings, store = _store(tmp_path)
    applied = store.update({"mnemos_llm_model": "qwen2.5:7b"}, SimpleNamespace())
    assert applied == []
    assert store.pending_restart() == ["mnemos_llm_model"]
    assert settings.mnemos_llm_model == "llama3.1:8b"  # nothing live
    assert _field(store.snapshot(), "mnemos_llm_model")["value"] == "qwen2.5:7b"

    # Saving the boot value back is not a pending change.
    store.update({"mnemos_llm_model": "llama3.1:8b"}, SimpleNamespace())
    assert store.pending_restart() == []


def test_reset_reverts_to_env_and_reapplies_live(tmp_path, clean_env):
    clean_env.setenv("MNEMOS_CACHE_THRESHOLD", "0.9")
    settings, store = _store(tmp_path, {"mnemos_cache_threshold": 0.5})
    cache = SimpleNamespace(threshold=0.5)

    store.reset("mnemos_cache_threshold", SimpleNamespace(cache=cache))

    assert settings.mnemos_cache_threshold == 0.9
    assert cache.threshold == 0.9
    assert json.loads((tmp_path / "settings.json").read_text()) == {}
    field = _field(store.snapshot(), "mnemos_cache_threshold")
    assert (field["value"], field["source"]) == (0.9, "env")
    with pytest.raises(KeyError):
        store.reset("mnemos_cache_threshold", SimpleNamespace())


def test_secret_value_never_in_snapshot(tmp_path, clean_env):
    clean_env.setenv("MNEMOS_LLM_API_KEY", "sk-SENTINEL-123")
    _, store = _store(tmp_path)
    snap = store.snapshot()
    key = _field(snap, "mnemos_llm_api_key")
    assert key["value"] is None and key["default"] is None
    assert key["is_set"] is True
    assert "SENTINEL" not in json.dumps(snap)
