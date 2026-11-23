"""Every server setting, described for the dashboard's configuration page.

Single source of truth for what the page shows and what it may change. Every
`Settings` field must have an entry (`tests/test_settings_registry.py`), so a
new setting cannot silently go missing from the page.

`apply` says how a change takes effect:
    live     — PATCH updates the running components immediately
    restart  — persisted, effective at the next process start
    reindex  — needs a re-embedding of the indexed collections
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

FieldType = Literal["bool", "int", "float", "str", "enum", "secret", "path", "url"]
Apply = Literal["live", "restart", "reindex"]

SECTIONS: tuple[tuple[str, str], ...] = (
    ("retrieval", "Retrieval"),
    ("cache", "Cache"),
    ("llm", "LLM"),
    ("memory", "Memory"),
    ("indexing", "Indexing"),
    ("observability", "Observability"),
    ("infrastructure", "Infrastructure"),
    ("security", "Security"),
)


@dataclass(frozen=True)
class FieldSpec:
    key: str
    section: str
    label: str
    description: str
    type: FieldType
    apply: Apply = "restart"
    editable: bool = False
    options: tuple[str, ...] | None = None
    min: float | None = None
    max: float | None = None
    # (app.state attribute, attribute on that component) a live change sets.
    live_target: tuple[str, str] | None = None

    @property
    def env(self) -> str:
        return self.key.upper()


def _live(key: str, section: str, label: str, description: str, type: FieldType,
          target: tuple[str, str], **kw: Any) -> FieldSpec:
    return FieldSpec(key, section, label, description, type, apply="live",
                     editable=True, live_target=target, **kw)


FIELDS: tuple[FieldSpec, ...] = (
    # --- retrieval ---------------------------------------------------------
    _live("mnemos_reranker_enabled", "retrieval", "Cross-encoder reranker",
          "Re-score candidates with a cross-encoder; biggest quality lever, slow on CPU.",
          "bool", ("reranker", "enabled")),
    FieldSpec("mnemos_reranker_model", "retrieval", "Reranker model",
              "Cross-encoder checkpoint, loaded on the first rerank.",
              "str", editable=True),
    FieldSpec("mnemos_reranker_type", "retrieval", "Reranker backend",
              "Reranker implementation the model runs on.", "str"),
    FieldSpec("mnemos_reranker_max_length", "retrieval", "Reranker max length",
              "Tokens per (query, chunk) pair; 0 keeps the model's limit. 256 halved latency at no measured loss.",
              "int", editable=True, min=0, max=8192),
    FieldSpec("mnemos_fusion_dense_weight", "retrieval", "Dense fusion weight",
              "Weight of the dense side in the dense/BM25 RRF fusion; 1.0 is plain RRF.",
              "float", editable=True, min=0.1, max=10.0),
    _live("mnemos_mmr_enabled", "retrieval", "MMR diversification",
          "Pick novel candidates for the top-K after reranking.",
          "bool", ("search_service", "mmr_enabled")),
    _live("mnemos_mmr_lambda", "retrieval", "MMR lambda",
          "0 = pure novelty, 1 = pure relevance.",
          "float", ("search_service", "mmr_lambda"), min=0.0, max=1.0),
    _live("mnemos_grader_enabled", "retrieval", "Document grader",
          "LLM judges each retrieved chunk; one LLM call per chunk.",
          "bool", ("grader", "enabled")),
    FieldSpec("mnemos_grader_workers", "retrieval", "Grader workers",
              "Parallel grading calls.", "int", editable=True, min=1, max=32),
    _live("mnemos_rewriter_enabled", "retrieval", "Query rewriter",
          "When every chunk grades low, rewrite the query and retry.",
          "bool", ("rewriter", "enabled")),
    _live("mnemos_rewriter_strategy", "retrieval", "Rewriter strategy",
          "expansion (synonyms), decompose (sub-questions) or hyde (hypothetical answer).",
          "enum", ("rewriter", "strategy"), options=("expansion", "decompose", "hyde")),
    _live("mnemos_rewriter_max_variants", "retrieval", "Rewriter variants",
          "Cap on alternative queries generated per failure.",
          "int", ("rewriter", "max_variants"), min=1, max=10),
    # --- cache -------------------------------------------------------------
    _live("mnemos_cache_enabled", "cache", "Semantic cache",
          "Replay results for queries close enough to one already seen.",
          "bool", ("cache", "enabled")),
    _live("mnemos_cache_threshold", "cache", "Cache threshold",
          "Cosine similarity needed for a cache hit.",
          "float", ("cache", "threshold"), min=0.0, max=1.0),
    _live("mnemos_cache_ttl_seconds", "cache", "Cache TTL",
          "Seconds before a cached entry expires.",
          "int", ("cache", "ttl_seconds"), min=0),
    # --- llm ---------------------------------------------------------------
    FieldSpec("mnemos_llm_provider", "llm", "LLM provider",
              "Provider used for memory extraction, contextual chunking and CRAG.",
              "enum", editable=True, options=("ollama", "anthropic", "openai")),
    FieldSpec("mnemos_llm_model", "llm", "LLM model",
              "Provider-specific model name.", "str", editable=True),
    FieldSpec("mnemos_llm_api_key", "llm", "LLM API key",
              "Required for anthropic and openai. Never returned by the API.", "secret"),
    FieldSpec("mnemos_llm_base_url", "llm", "LLM base URL",
              "Generic base URL (OpenAI-compatible endpoints); overrides the Ollama URL.", "url"),
    FieldSpec("mnemos_ollama_url", "llm", "Ollama URL",
              "Ollama endpoint when no base URL is set; auto-detected when blank.", "url"),
    # --- memory ------------------------------------------------------------
    _live("mnemos_dedup_threshold", "memory", "Dedup threshold",
          "Cosine similarity above which a new memory merges into an existing one.",
          "float", ("deduplicator", "threshold"), min=0.0, max=1.0),
    _live("mnemos_dedup_strategy", "memory", "Dedup strategy",
          "merge (LLM consolidates) or replace (newer wins).",
          "enum", ("deduplicator", "strategy"), options=("merge", "replace")),
    FieldSpec("mnemos_hook_trigger", "memory", "Hook trigger",
              "Git hook that triggers memory extraction (read by the install script).", "str"),
    # --- indexing ----------------------------------------------------------
    _live("mnemos_contextual_enabled", "indexing", "Contextual chunking",
          "Prepend an LLM preamble to each chunk; affects the next indexing only.",
          "bool", ("contextual", "enabled")),
    FieldSpec("mnemos_contextual_workers", "indexing", "Contextual workers",
              "Parallel LLM calls during indexing.", "int", editable=True, min=1, max=32),
    FieldSpec("mnemos_index_only_declared_paths", "indexing", "Index declared paths only",
              "Index only the prefixes the Projects page declares.", "bool", editable=True),
    FieldSpec("embedding_model", "indexing", "Embedding model",
              "Changing it on an existing index needs a migration.", "str", apply="reindex"),
    FieldSpec("mnemos_max_chunk_chars", "indexing", "Max chunk size",
              "Upper bound on a chunk, in characters; 0 keeps the chunkers' boundaries. Needs a rebuild.",
              "int", apply="reindex"),
    FieldSpec("codebase_path", "indexing", "Codebase path",
              "Container path of the mounted codebase.", "path"),
    FieldSpec("claude_config_path", "indexing", "Claude config path",
              "Container path of the mounted Claude config (skills, docs).", "path"),
    FieldSpec("mnemos_projects_config_path", "indexing", "Projects import file",
              "projects.yaml imported once, when the database holds no projects; edit them on the Projects page.",
              "path"),
    FieldSpec("mnemos_connectors_config", "indexing", "Connectors import file",
              "connectors.yaml imported once, when the database holds no connectors; edit them on the Connectors page.",
              "path"),
    # --- observability -----------------------------------------------------
    _live("mnemos_query_log_enabled", "observability", "Query log",
          "Append every retrieval call to a JSONL log; required by the Search screen.",
          "bool", ("query_logger", "enabled")),
    FieldSpec("mnemos_query_log_path", "observability", "Query log path",
              "Query log location inside the container.", "path"),
    FieldSpec("mnemos_eval_runs_path", "observability", "Eval runs path",
              "Where the dashboard reads eval runs.", "path"),
    # --- infrastructure ----------------------------------------------------
    FieldSpec("qdrant_host", "infrastructure", "Qdrant host", "Qdrant hostname.", "str"),
    FieldSpec("qdrant_port", "infrastructure", "Qdrant port", "Qdrant HTTP port.", "int"),
    FieldSpec("qdrant_api_key", "infrastructure", "Qdrant API key",
              "Sent to Qdrant; required when authentication is on. Never returned by the API.",
              "secret"),
    FieldSpec("qdrant_https", "infrastructure", "Qdrant over TLS",
              "Connect to Qdrant with https.", "bool"),
    FieldSpec("mnemos_state_dir", "infrastructure", "State directory",
              "Query log; its settings.json is imported once, when the database holds no settings overrides.",
              "path"),
    FieldSpec("mnemos_config_poll_seconds", "infrastructure", "Config poll interval",
              "Seconds between two checks of the shared configuration; edits on another pod land within it.",
              "float"),
    FieldSpec("mnemos_ui_path", "infrastructure", "Dashboard path",
              "Built dashboard; absent means the server serves the API only.", "path"),
    FieldSpec("mnemos_ui_theme_path", "infrastructure", "Theme stylesheet",
              "Instance CSS served at /theme.css; absent means no override.", "path"),
    # --- security ----------------------------------------------------------
    FieldSpec("mnemos_auth_enabled", "security", "Authentication",
              "Require a bearer API key on /api, /internal and /mcp.", "bool"),
    FieldSpec("mnemos_tenants_path", "security", "Tenants import file",
              "tenants.yaml imported once (as admins), when the database holds no tenant.", "path"),
    FieldSpec("mnemos_bootstrap_admin_key", "security", "Bootstrap admin key",
              "Creates tenant `admin` at first start when the database holds no tenant and no tenants file exists. Never returned by the API.",
              "secret"),
    FieldSpec("mnemos_ui_config_writable", "security", "Dashboard can edit config",
              "Allow writes to settings, connectors, projects and tenants; with auth off anyone reaching the server can.",
              "bool"),
)

FIELDS_BY_KEY: dict[str, FieldSpec] = {f.key: f for f in FIELDS}


def coerce(spec: FieldSpec, value: Any) -> Any:
    """Validate `value` for `spec`; return it normalised or raise ValueError.

    Strict on types: JSON `"true"` is not a bool, `1.5` is not an int.
    """
    t = spec.type
    if t == "bool":
        if not isinstance(value, bool):
            raise ValueError("expected a boolean")
        return value
    if t in ("int", "float"):
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError(f"expected {'an integer' if t == 'int' else 'a number'}")
        if t == "int" and not isinstance(value, int):
            raise ValueError("expected an integer")
        value = int(value) if t == "int" else float(value)
        if spec.min is not None and value < spec.min:
            raise ValueError(f"must be >= {spec.min:g}")
        if spec.max is not None and value > spec.max:
            raise ValueError(f"must be <= {spec.max:g}")
        return value
    if t == "enum":
        if value not in (spec.options or ()):
            raise ValueError(f"must be one of {', '.join(spec.options or ())}")
        return value
    if not isinstance(value, str) or not value.strip():
        raise ValueError("expected a non-empty string")
    return value.strip()
