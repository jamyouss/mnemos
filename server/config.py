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
    mnemos_llm_provider: str = "ollama"
    mnemos_llm_model: str = "llama3.1:8b"
    mnemos_ollama_url: str = "http://localhost:11434"
    mnemos_hook_trigger: str = "pre-push"
    mnemos_dedup_threshold: float = 0.85
    mnemos_dedup_strategy: str = "merge"

    class Config:
        env_prefix = ""


settings = Settings()
