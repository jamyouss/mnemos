from __future__ import annotations

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    qdrant_host: str = "localhost"
    qdrant_port: int = 6333
    embedding_model: str = "all-MiniLM-L6-v2"
    codebase_path: str = "/data/codebase"
    claude_config_path: str = "/data/claude-config"
    rag_mode: str = "local"
    rag_auth_enabled: bool = False
    rag_state_dir: str = "/data/state"

    class Config:
        env_prefix = ""


settings = Settings()
