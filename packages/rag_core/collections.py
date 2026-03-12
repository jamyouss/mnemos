from __future__ import annotations

from dataclasses import dataclass


@dataclass
class CollectionConfig:
    name: str
    vector_size: int = 384
    path_prefixes: list[str] | None = None
    description: str = ""


COLLECTIONS = [
    CollectionConfig(
        name="rag_skills",
        path_prefixes=["skills/"],
        description="Claude Code skills (metadata + instructions)",
    ),
    CollectionConfig(
        name="rag_docs",
        path_prefixes=["docs/"],
        description="Architecture and pattern documentation",
    ),
    CollectionConfig(
        name="rag_memory",
        path_prefixes=None,
        description="Conversation memory entries",
    ),
    CollectionConfig(
        name="rag_code_moby",
        path_prefixes=["moby/"],
        description="Moby application codebase",
    ),
    CollectionConfig(
        name="rag_code_trevio",
        path_prefixes=["trevio/"],
        description="Trevio platform codebase",
    ),
    CollectionConfig(
        name="rag_code_infra",
        path_prefixes=["infra/", "github-cicd/"],
        description="Infrastructure and CI/CD",
    ),
]


def get_collection_for_path(file_path: str) -> str | None:
    for config in COLLECTIONS:
        if config.path_prefixes is None:
            continue
        for prefix in config.path_prefixes:
            if file_path.startswith(prefix):
                return config.name
    return None
