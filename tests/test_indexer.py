from unittest.mock import MagicMock

import pytest

from core.indexer import Indexer


@pytest.fixture
def mock_qdrant():
    client = MagicMock()
    client.get_collections.return_value.collections = []
    return client


@pytest.fixture
def mock_embeddings():
    service = MagicMock()
    service.embed.return_value = [0.1] * 384
    service.embed_batch.return_value = [[0.1] * 384, [0.2] * 384]
    return service


@pytest.fixture
def indexer(mock_qdrant, mock_embeddings):
    return Indexer(qdrant_client=mock_qdrant, embedding_service=mock_embeddings)


def test_select_chunker_for_go(indexer):
    from core.chunkers.go_chunker import GoChunker
    chunker = indexer._select_chunker("service.go")
    assert isinstance(chunker, GoChunker)


def test_select_chunker_for_vue(indexer):
    from core.chunkers.vue_chunker import VueChunker
    chunker = indexer._select_chunker("Component.vue")
    assert isinstance(chunker, VueChunker)


def test_select_chunker_for_markdown(indexer):
    from core.chunkers.markdown_chunker import MarkdownChunker
    chunker = indexer._select_chunker("README.md")
    assert isinstance(chunker, MarkdownChunker)


def test_select_chunker_fallback(indexer):
    from core.chunkers.fallback_chunker import FallbackChunker
    chunker = indexer._select_chunker("config.yaml")
    assert isinstance(chunker, FallbackChunker)


def test_index_file_writes_tags_from_path(mock_qdrant, mock_embeddings, sample_go_code):
    """For mnemos_code, the tag list is derived from the path by default."""
    indexer = Indexer(
        qdrant_client=mock_qdrant,
        embedding_service=mock_embeddings,
        codebase_root="/data/codebase",
    )
    indexer.index_file(
        content=sample_go_code,
        file_path="/data/codebase/myproject/services/core/app.go",
        collection="mnemos_code",
    )
    mock_qdrant.upsert.assert_called_once()
    points = mock_qdrant.upsert.call_args.kwargs["points"]
    assert len(points) > 0
    # Cumulative directory tags (filename excluded).
    expected = ["myproject", "myproject/services", "myproject/services/core"]
    for p in points:
        assert p.payload.get("tags") == expected


def test_index_file_uses_path_tags_override(mock_qdrant, mock_embeddings, sample_go_code):
    """When config/projects.yaml supplies tags for a prefix, they win over
    the default segment-based detection."""
    indexer = Indexer(
        qdrant_client=mock_qdrant,
        embedding_service=mock_embeddings,
        path_tags={
            "myproject/services/": ["my-service", "myproject", "go"],
        },
        codebase_root="/data/codebase",
    )
    indexer.index_file(
        content=sample_go_code,
        file_path="/data/codebase/myproject/services/core/app.go",
        collection="mnemos_code",
    )
    points = mock_qdrant.upsert.call_args.kwargs["points"]
    for p in points:
        assert p.payload.get("tags") == ["my-service", "myproject", "go"]


def test_index_file_explicit_tags_override_wins(mock_qdrant, mock_embeddings, sample_go_code):
    """An explicit tags=... on index_file() bypasses both the YAML override
    and the default segment-based detection."""
    indexer = Indexer(
        qdrant_client=mock_qdrant,
        embedding_service=mock_embeddings,
        path_tags={"myproject/": ["yaml-tag"]},
        codebase_root="/data/codebase",
    )
    indexer.index_file(
        content=sample_go_code,
        file_path="/data/codebase/myproject/services/core/app.go",
        collection="mnemos_code",
        tags=["override-a", "override-b"],
    )
    points = mock_qdrant.upsert.call_args.kwargs["points"]
    for p in points:
        assert p.payload.get("tags") == ["override-a", "override-b"]


def test_index_file_skips_tags_for_skills_and_docs(indexer, mock_qdrant, sample_go_code):
    indexer.index_file(
        content=sample_go_code,
        file_path="skills/some-skill/instructions.md",
        collection="mnemos_skills",
    )
    points = mock_qdrant.upsert.call_args.kwargs["points"]
    for p in points:
        assert "tags" not in p.payload


def test_index_file_skips_filtered_path(indexer, mock_qdrant, mock_embeddings, sample_go_code):
    """A vendored ``.cjs`` bundle must never reach the embedder, even if a
    caller bypassed the upstream watcher / bulk-reindex filter."""
    result = indexer.index_file(
        content=sample_go_code,
        file_path="/data/codebase/x/.yarn/releases/yarn-4.9.4.cjs",
        collection="mnemos_code",
    )
    assert result == 0
    mock_embeddings.embed_batch.assert_not_called()
    mock_qdrant.upsert.assert_not_called()


def test_index_file_skips_report_html(indexer, mock_qdrant, mock_embeddings, sample_go_code):
    result = indexer.index_file(
        content=sample_go_code,
        file_path="/data/codebase/x/others/report-tnr-gherkin-analysis.html",
        collection="mnemos_code",
    )
    assert result == 0
    mock_qdrant.upsert.assert_not_called()


def test_delete_file(indexer, mock_qdrant):
    indexer.delete_file(
        file_path="myproject/services/core/old.go",
        collection="mnemos_code",
    )
    mock_qdrant.delete.assert_called_once()


# ---------------------------------------------------------------------------
# Per-prefix ignore rules from config/projects.yaml
# ---------------------------------------------------------------------------


EXCLUDES = {
    "Projects/acme/reports/": {"exts": [".csv"], "dirs": ["exports"]},
}


@pytest.fixture
def indexer_with_excludes(mock_qdrant, mock_embeddings):
    return Indexer(
        qdrant_client=mock_qdrant,
        embedding_service=mock_embeddings,
        path_excludes=EXCLUDES,
        codebase_root="/data/codebase",
    )


def test_config_excludes_apply_under_their_prefix(indexer_with_excludes):
    assert indexer_with_excludes.should_skip(
        "/data/codebase/Projects/acme/reports/q1.csv"
    ) is True
    assert indexer_with_excludes.should_skip(
        "/data/codebase/Projects/acme/reports/exports/anything.txt"
    ) is True


def test_config_excludes_do_not_leak_outside_their_prefix(indexer_with_excludes):
    """The same extension elsewhere in the tree stays indexable — that is the
    whole point of putting the rule on a path prefix."""
    assert indexer_with_excludes.should_skip(
        "/data/codebase/Projects/other/data/q1.csv"
    ) is False


def test_builtin_policy_still_applies(indexer_with_excludes):
    assert indexer_with_excludes.should_skip(
        "/data/codebase/Projects/acme/app/node_modules/pkg/index.js"
    ) is True


def test_caller_extras_union_with_config(indexer_with_excludes):
    """--exclude-ext adds to the config rules; it does not replace them."""
    path = "/data/codebase/Projects/acme/reports/schema.proto"
    assert indexer_with_excludes.should_skip(path) is False
    assert indexer_with_excludes.should_skip(path, extra_exts=[".proto"]) is True
    # The config rule is still in force alongside the extra.
    assert indexer_with_excludes.should_skip(
        "/data/codebase/Projects/acme/reports/q1.csv", extra_exts=[".proto"]
    ) is True


def test_index_file_honours_config_excludes(indexer_with_excludes, mock_qdrant):
    """The durability guarantee: index_file is the chokepoint every ingestion
    path funnels through, so a config exclude holds for the watcher and the
    push API too — not just for a reindex run that passed the flags."""
    written = indexer_with_excludes.index_file(
        content="id,city\n1,paris\n",
        file_path="/data/codebase/Projects/acme/reports/q1.csv",
        collection="mnemos_code",
    )
    assert written == 0
    mock_qdrant.upsert.assert_not_called()


def test_index_file_indexes_the_same_name_outside_the_prefix(indexer_with_excludes):
    written = indexer_with_excludes.index_file(
        content="id,city\n1,paris\n",
        file_path="/data/codebase/Projects/other/data/q1.csv",
        collection="mnemos_code",
    )
    assert written > 0


def test_no_config_means_builtin_policy_only(mock_qdrant, mock_embeddings):
    plain = Indexer(qdrant_client=mock_qdrant, embedding_service=mock_embeddings)
    assert plain.should_skip("/data/codebase/Projects/acme/reports/q1.csv") is False
