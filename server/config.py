from __future__ import annotations

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    qdrant_host: str = "localhost"
    qdrant_port: int = 6333
    embedding_model: str = "all-MiniLM-L6-v2"
    codebase_path: str = "/data/codebase"
    claude_config_path: str = "/data/claude-config"
    # Require `Authorization: Bearer <key>` on /api, /internal and /mcp; keys
    # come from the tenants file. The server refuses to start if it is unusable.
    mnemos_auth_enabled: bool = False
    mnemos_tenants_path: str = "/data/config/tenants.yaml"
    mnemos_state_dir: str = "/data/state"
    # LLM configuration (ollama | anthropic | openai)
    mnemos_llm_provider: str = "ollama"
    mnemos_llm_model: str = "llama3.1:8b"
    mnemos_llm_api_key: str = ""               # required for anthropic + openai
    mnemos_llm_base_url: str = ""              # generic base url (overrides ollama_url when set)
    mnemos_ollama_url: str = "http://localhost:11434"
    mnemos_hook_trigger: str = "pre-push"
    mnemos_dedup_threshold: float = 0.85
    mnemos_dedup_strategy: str = "merge"

    # Contextual retrieval (Anthropic-style preamble per chunk)
    mnemos_contextual_enabled: bool = False
    mnemos_contextual_workers: int = 4
    # Upper bound on a chunk, in characters; 0 keeps the chunkers' own
    # boundaries. Off on purpose: splitting at 1 500 (what the embedder reads)
    # lowered MRR 0.835 → 0.789, more chunks meaning more near-misses
    # (docs/EVAL.md). Changing it needs a rebuild: core.embedding_migration
    # --max-chunk-chars.
    mnemos_max_chunk_chars: int = 0

    # Cross-encoder reranker (Phase 2B)
    mnemos_reranker_enabled: bool = False
    mnemos_reranker_model: str = "BAAI/bge-reranker-base"
    mnemos_reranker_type: str = "cross-encoder"
    # Tokens per (query, chunk) pair. 256 halved bge-reranker-base latency with
    # no measurable loss (docs/EVAL.md); 0 keeps the model's own limit.
    mnemos_reranker_max_length: int = 256
    # Dense weight in the dense/BM25 RRF fusion. 1.0 is plain RRF; 3.0 lifted
    # code-search MRR from 0.55 to 0.74 (docs/EVAL.md).
    mnemos_fusion_dense_weight: float = 3.0
    mnemos_mmr_enabled: bool = False
    mnemos_mmr_lambda: float = 0.5

    # CRAG corrective loop (Phase 3): document grader + query rewriter
    mnemos_grader_enabled: bool = False
    mnemos_grader_workers: int = 4
    mnemos_rewriter_enabled: bool = False
    mnemos_rewriter_strategy: str = "expansion"     # expansion | decompose | hyde
    mnemos_rewriter_max_variants: int = 3


    # Semantic cache (Phase 4E)
    mnemos_cache_enabled: bool = False
    mnemos_cache_threshold: float = 0.95
    mnemos_cache_ttl_seconds: int = 3600

    # Observability (Phase 4 / 9): query logging JSONL
    mnemos_query_log_enabled: bool = False
    mnemos_query_log_path: str = "/data/state/query-log.jsonl"
    # Eval runs are local artefacts, never baked into the image — bind-mounted
    # read-only so the dashboard can read them.
    mnemos_eval_runs_path: str = "/app/evals/runs"
    # Built dashboard. Absent in a headless deployment, and the server must
    # start anyway.
    mnemos_ui_path: str = "/app/ui"

    # Project detection (auto-detect + optional YAML override)
    mnemos_projects_config_path: str = "/app/config/projects.yaml"
    # Index only what config/projects.yaml declares. Off by default so an
    # empty or absent config keeps indexing everything, as before.
    mnemos_index_only_declared_paths: bool = False

    class Config:
        env_prefix = ""


settings = Settings()
