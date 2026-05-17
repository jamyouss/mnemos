from __future__ import annotations

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    qdrant_host: str = "localhost"
    qdrant_port: int = 6333
    embedding_model: str = "all-MiniLM-L6-v2"
    codebase_path: str = "/data/codebase"
    claude_config_path: str = "/data/claude-config"
    mnemos_mode: str = "local"
    mnemos_auth_enabled: bool = False
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

    # Cross-encoder reranker (Phase 2B)
    mnemos_reranker_enabled: bool = False
    mnemos_reranker_model: str = "BAAI/bge-reranker-base"
    mnemos_reranker_type: str = "cross-encoder"
    mnemos_mmr_enabled: bool = False
    mnemos_mmr_lambda: float = 0.5

    # CRAG corrective loop (Phase 3): document grader + query rewriter
    mnemos_grader_enabled: bool = False
    mnemos_grader_workers: int = 4
    mnemos_rewriter_enabled: bool = False
    mnemos_rewriter_strategy: str = "expansion"     # expansion | decompose | hyde
    mnemos_rewriter_max_variants: int = 3

    # Semantic router (Phase 4D)
    mnemos_router_enabled: bool = False
    mnemos_router_top_k: int = 2
    mnemos_router_min_score: float = 0.4

    # Semantic cache (Phase 4E)
    mnemos_cache_enabled: bool = False
    mnemos_cache_threshold: float = 0.95
    mnemos_cache_ttl_seconds: int = 3600

    # Observability (Phase 4 / 9): query logging JSONL
    mnemos_query_log_enabled: bool = False
    mnemos_query_log_path: str = "/data/state/query-log.jsonl"

    # Project detection (auto-detect + optional YAML override)
    mnemos_projects_config_path: str = "/app/config/projects.yaml"

    class Config:
        env_prefix = ""


settings = Settings()
