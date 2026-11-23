from __future__ import annotations

import pytest

from server.config import Settings
from server.settings_registry import FIELDS, FIELDS_BY_KEY, SECTIONS, coerce

# The contract of the dashboard's config page: these and only these change.
EDITABLE = {
    "mnemos_reranker_enabled": "live",
    "mnemos_reranker_model": "restart",
    "mnemos_reranker_max_length": "restart",
    "mnemos_fusion_dense_weight": "restart",
    "mnemos_mmr_enabled": "live",
    "mnemos_mmr_lambda": "live",
    "mnemos_grader_enabled": "live",
    "mnemos_grader_workers": "restart",
    "mnemos_rewriter_enabled": "live",
    "mnemos_rewriter_strategy": "live",
    "mnemos_rewriter_max_variants": "live",
    "mnemos_cache_enabled": "live",
    "mnemos_cache_threshold": "live",
    "mnemos_cache_ttl_seconds": "live",
    "mnemos_query_log_enabled": "live",
    "mnemos_contextual_enabled": "live",
    "mnemos_contextual_workers": "restart",
    "mnemos_dedup_threshold": "live",
    "mnemos_dedup_strategy": "live",
    "mnemos_llm_provider": "restart",
    "mnemos_llm_model": "restart",
    "mnemos_index_only_declared_paths": "restart",
}


def test_every_setting_is_in_the_registry():
    """A new setting must not silently miss the config page."""
    assert set(Settings.model_fields) == set(FIELDS_BY_KEY)
    assert len(FIELDS) == len(FIELDS_BY_KEY)


def test_editable_fields_match_the_contract():
    editable = {f.key: f.apply for f in FIELDS if f.editable}
    assert editable == EDITABLE


@pytest.mark.parametrize("key", [
    "mnemos_llm_api_key", "mnemos_llm_base_url", "mnemos_ollama_url", "qdrant_host",
    "qdrant_port", "qdrant_api_key", "qdrant_https", "embedding_model", "mnemos_auth_enabled",
    "mnemos_tenants_path", "mnemos_bootstrap_admin_key", "mnemos_config_poll_seconds",
    "mnemos_projects_config_path", "mnemos_connectors_config",
    "mnemos_state_dir", "mnemos_ui_config_writable", "mnemos_ui_theme_path",
])
def test_sensitive_fields_are_read_only(key):
    assert FIELDS_BY_KEY[key].editable is False


def test_api_key_is_a_secret_and_embedding_model_needs_a_reindex():
    assert FIELDS_BY_KEY["mnemos_llm_api_key"].type == "secret"
    assert FIELDS_BY_KEY["qdrant_api_key"].type == "secret"
    assert FIELDS_BY_KEY["mnemos_bootstrap_admin_key"].type == "secret"
    assert FIELDS_BY_KEY["embedding_model"].apply == "reindex"


def test_every_live_field_targets_a_component():
    for f in FIELDS:
        assert (f.apply == "live") == (f.live_target is not None), f.key


def test_fields_belong_to_known_sections():
    section_ids = {s for s, _ in SECTIONS}
    assert {f.section for f in FIELDS} <= section_ids


def test_env_name_is_the_upper_key():
    assert FIELDS_BY_KEY["mnemos_cache_enabled"].env == "MNEMOS_CACHE_ENABLED"


@pytest.mark.parametrize("key,value,expected", [
    ("mnemos_cache_enabled", True, True),
    ("mnemos_mmr_lambda", 1, 1.0),
    ("mnemos_grader_workers", 8, 8),
    ("mnemos_rewriter_strategy", "hyde", "hyde"),
    ("mnemos_llm_model", "  qwen2.5:7b ", "qwen2.5:7b"),
])
def test_coerce_accepts_valid_values(key, value, expected):
    assert coerce(FIELDS_BY_KEY[key], value) == expected


@pytest.mark.parametrize("key,value", [
    ("mnemos_cache_enabled", "true"),        # strings are not booleans
    ("mnemos_grader_workers", True),         # nor are booleans integers
    ("mnemos_grader_workers", 2.5),
    ("mnemos_grader_workers", 0),            # below min
    ("mnemos_grader_workers", 33),           # above max
    ("mnemos_mmr_lambda", 1.5),
    ("mnemos_cache_ttl_seconds", -1),
    ("mnemos_rewriter_strategy", "magic"),
    ("mnemos_llm_model", "   "),
])
def test_coerce_rejects_invalid_values(key, value):
    with pytest.raises(ValueError):
        coerce(FIELDS_BY_KEY[key], value)
