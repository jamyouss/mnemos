from __future__ import annotations

from dataclasses import dataclass


# Named-vector identifiers used in every collection that supports hybrid retrieval.
DENSE_VECTOR_NAME = "dense"
SPARSE_VECTOR_NAME = "sparse"


@dataclass
class CollectionConfig:
    name: str
    vector_size: int = 384
    path_prefixes: list[str] | None = None
    description: str = ""


# Default collection layout. Add a `mnemos_code_<project>` entry per repo you
# want to index — both the filesystem watcher and the semantic QueryRouter
# pick up additions automatically.
COLLECTIONS = [
    CollectionConfig(
        name="mnemos_skills",
        path_prefixes=["skills/"],
        description="Agent skills (metadata + instructions)",
    ),
    CollectionConfig(
        name="mnemos_docs",
        path_prefixes=["docs/"],
        description="Architecture and pattern documentation",
    ),
    CollectionConfig(
        name="mnemos_memory",
        path_prefixes=None,
        description="Memories extracted from commits and conversations",
    ),
    # Example collections — duplicate / rename per project you want to index.
    # The path_prefixes match the path relative to /data/codebase inside the
    # container. Add as many as you need.
    CollectionConfig(
        name="mnemos_code_myproject",
        path_prefixes=["myproject/"],
        description="Example application codebase — rename to your repo",
    ),
    CollectionConfig(
        name="mnemos_code_otherproject",
        path_prefixes=["otherproject/"],
        description="Second example codebase",
    ),
    CollectionConfig(
        name="mnemos_code_infra",
        path_prefixes=["infra/", "ci/"],
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
