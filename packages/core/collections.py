from __future__ import annotations

from dataclasses import dataclass


# Named-vector identifiers used in every collection that supports hybrid retrieval.
DENSE_VECTOR_NAME = "dense"
SPARSE_VECTOR_NAME = "sparse"

# Multi-value scoping field. Each chunk carries a list of tags (primary
# project name + any cross-cutting labels declared in config/projects.yaml).
# Filtered at query time with MatchAny (OR) or AND-combined conditions.
TAGS_PAYLOAD_FIELD = "tags"

# Server configuration documents (connectors, projects, settings, tenants),
# shared by every pod. Vectorless and kept out of COLLECTIONS: nothing in it
# is searched, embedded, reindexed or migrated.
CONFIG_COLLECTION = "mnemos_config"


@dataclass
class CollectionConfig:
    name: str
    path_prefixes: list[str] | None = None
    description: str = ""


# One collection per kind of content — fixed. New projects or new sources are
# NOT new collections; they are tagged via the `tags` payload field on
# `mnemos_code`, `mnemos_knowledge` or `mnemos_memory` and filtered at query
# time. See docs/RETRIEVAL_PIPELINE.md.
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
        name="mnemos_code",
        path_prefixes=None,
        description="Source code across all projects (scope with tags filter)",
    ),
    CollectionConfig(
        name="mnemos_memory",
        path_prefixes=None,
        description="Memories extracted from commits and conversations",
    ),
    CollectionConfig(
        name="mnemos_knowledge",
        path_prefixes=None,
        description="External knowledge pushed by connectors: wiki pages, tickets, web sites (scope with tags filter)",
    ),
]


def get_collection_for_path(file_path: str) -> str | None:
    """Decide which collection a `file_path` belongs to.

    - `skills/...` → mnemos_skills
    - `docs/...`   → mnemos_docs
    - everything else under the codebase mount → mnemos_code
      (tag assignment happens at index time, not collection routing)
    - returns None for empty / unmappable paths
    """
    if not file_path:
        return None
    for config in COLLECTIONS:
        if config.path_prefixes is None:
            continue
        for prefix in config.path_prefixes:
            if file_path.startswith(prefix):
                return config.name
    # Default: anything that looks like a real path is code.
    if "/" in file_path and not file_path.startswith("/"):
        return "mnemos_code"
    return None
