"""Connector file paths must survive the server's path filter.

``Indexer.index_file`` silently drops whatever ``Indexer.should_skip``
refuses (vendored dirs, build outputs…). A connector document dropped there
would be re-pushed on every run and never be searchable, so every path
scheme is checked against the real filter here.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from connectors import confluence, jira, web
from core.indexer import Indexer

# Scope keys that spell a directory name the filter refuses, in any case.
RISKY_KEYS = ["DIST", "BUILD", "ARCHIVED", "VENDOR", "BACKUP", "COVERAGE", "PODS", "VENV", "WORKTREES"]


@pytest.fixture
def indexer() -> Indexer:
    qdrant = MagicMock()
    qdrant.collection_exists.return_value = False
    embeddings = MagicMock()
    embeddings.dimension = 384
    embeddings.embed_documents.return_value = [[0.1] * 384]
    return Indexer(qdrant_client=qdrant, embedding_service=embeddings)


def _paths() -> list[str]:
    paths: list[str] = []
    for key in RISKY_KEYS:
        for typed in (key, key.lower(), key.title()):
            space = confluence.scope_key(typed)
            paths.append(confluence.file_path(space, "65601"))
            project = jira.scope_key(typed)
            paths.append(jira.file_path(project, f"{project}-12"))
    paths.append(confluence.file_path(confluence.scope_key("~jdoe"), "1"))
    for url in (
        "https://docs.example.com/guide/",
        "https://build.example.com/",
        "http://build/",
        "http://dist/index.html",
        "http://vendor:8080/docs/",
        "http://archived/",
        "http://coverage/",
        "http://site-packages/",
    ):
        paths.append(web.file_path(web.normalize_url(url)))
    return paths


@pytest.mark.parametrize("path", _paths())
def test_generated_paths_are_never_skipped(indexer, path):
    assert not indexer.should_skip(path), path


def test_why_single_label_hosts_get_a_trailing_dot(indexer):
    # The bare host would land in a refused directory; the scheme avoids it.
    assert indexer.should_skip("web/build/0123456789abcdef.md")
    assert web.file_path("http://build/").startswith("web/build./")


def test_lowercase_keys_would_be_refused_without_normalisation(indexer):
    assert indexer.should_skip("confluence/dist/1.md")
    assert confluence.file_path(confluence.scope_key("dist"), "1") == "confluence/DIST/1.md"
    assert jira.file_path(jira.scope_key("vendor"), "VENDOR-1") == "jira/VENDOR/VENDOR-1.md"
