from unittest.mock import MagicMock

import pytest

from core.indexer import Indexer


@pytest.fixture
def mock_qdrant():
    client = MagicMock()
    client.collection_exists.return_value = False
    return client


@pytest.fixture
def mock_embeddings():
    service = MagicMock()
    service.dimension = 384
    service.embed_documents.return_value = [[0.1] * 384, [0.2] * 384]
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


def test_set_project_rules_replaces_tags_and_excludes(indexer):
    """The config sync swaps the projects mapping on a live indexer."""
    path = "/data/codebase/myproject/reports/exports/q1.csv"
    assert indexer.resolve_tags(path, None) == [
        "myproject", "myproject/reports", "myproject/reports/exports",
    ]
    assert indexer.should_skip(path) is False

    indexer.set_project_rules(
        {"myproject/": ["mine"]},
        {"myproject/reports/": {"exts": [], "dirs": ["exports"]}},
    )

    assert indexer.resolve_tags(path, None) == ["mine"]
    assert indexer.resolve_tags(path, ["explicit"]) == ["explicit"]
    assert indexer.should_skip(path) is True


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
    mock_embeddings.embed_documents.assert_not_called()
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


# ---------------------------------------------------------------------------
# Allowlist mode: index only what projects.yaml declares
# ---------------------------------------------------------------------------


@pytest.fixture
def allowlisted_indexer(mock_qdrant, mock_embeddings):
    return Indexer(
        qdrant_client=mock_qdrant,
        embedding_service=mock_embeddings,
        path_tags={"Projects/acme/": ["acme"], "Infra/docker/": ["infra-docker"]},
        only_declared_paths=True,
        codebase_root="/data/codebase",
    )


def test_allowlist_rejects_undeclared_trees(allowlisted_indexer):
    """The watcher walks the whole mount and knows nothing about which repos
    were meant to be indexed. Without this the server accepts everything it
    is handed — third-party clones included."""
    assert allowlisted_indexer.should_skip("/data/codebase/OpenSource/lib/main.go") is True
    assert allowlisted_indexer.should_skip("/data/codebase/Backup/old/app.ts") is True


def test_allowlist_keeps_declared_trees(allowlisted_indexer):
    assert allowlisted_indexer.should_skip("/data/codebase/Projects/acme/app/x.vue") is False
    assert allowlisted_indexer.should_skip("/data/codebase/Infra/docker/php/Dockerfile") is False


def test_allowlist_does_not_touch_skills_and_docs(allowlisted_indexer):
    """Skills and docs live under a different mount root; the allowlist is
    scoped to the codebase and must not silently swallow them."""
    assert allowlisted_indexer.should_skip("/data/claude-config/skills/a/b.md") is False
    assert allowlisted_indexer.should_skip("/data/claude-config/docs/arch.md") is False


def test_allowlist_matches_on_a_path_boundary(allowlisted_indexer):
    """`Projects/acme/` must not swallow a sibling `Projects/acme-legacy/`."""
    assert allowlisted_indexer.should_skip("/data/codebase/Projects/acme-legacy/x.go") is True


def test_allowlist_off_by_default(mock_qdrant, mock_embeddings):
    plain = Indexer(
        qdrant_client=mock_qdrant,
        embedding_service=mock_embeddings,
        path_tags={"Projects/acme/": ["acme"]},
        codebase_root="/data/codebase",
    )
    assert plain.should_skip("/data/codebase/OpenSource/lib/main.go") is False


# ---------------------------------------------------------------------------
# Skill metadata reaches the payload
# ---------------------------------------------------------------------------


def test_skill_chunks_carry_their_skill_name(mock_qdrant, mock_embeddings):
    """SkillResult reads skill_name at query time. Nothing wrote it, so
    mnemos_search_skills returned the right content under an empty name."""
    idx = Indexer(qdrant_client=mock_qdrant, embedding_service=mock_embeddings)
    idx.index_file(
        content="---\nname: docs-craft\ndescription: Write good docs.\n---\n\n# Docs\n",
        file_path="/data/claude-config/skills/docs-craft/SKILL.md",
        collection="mnemos_skills",
    )
    payload = mock_qdrant.upsert.call_args.kwargs["points"][0].payload
    assert payload["skill_name"] == "docs-craft"
    assert payload["description"] == "Write good docs."


def test_code_chunks_get_no_skill_fields(mock_qdrant, mock_embeddings):
    idx = Indexer(qdrant_client=mock_qdrant, embedding_service=mock_embeddings)
    idx.index_file(
        content="package main\n\nfunc main() {}\n",
        file_path="/data/codebase/Projects/acme/main.go",
        collection="mnemos_code",
    )
    payload = mock_qdrant.upsert.call_args.kwargs["points"][0].payload
    assert "skill_name" not in payload
    assert "description" not in payload


def test_knowledge_collection_is_tagged_and_keeps_the_source_url(mock_qdrant, mock_embeddings):
    """External sources (wiki, tickets, sites) are scoped by tags and must
    point back to where the page lives."""
    indexer = Indexer(qdrant_client=mock_qdrant, embedding_service=mock_embeddings)
    indexer.index_file(
        content="# Onboarding\n\nRead this first.\n",
        file_path="confluence/ENG/12345.md",
        collection="mnemos_knowledge",
        tags=["confluence", "confluence:ENG"],
        source_url="https://wiki.example.com/pages/12345",
        file_mtime=1700000000.0,
    )
    points = mock_qdrant.upsert.call_args.kwargs["points"]
    assert points
    for p in points:
        assert p.payload["tags"] == ["confluence", "confluence:ENG"]
        assert p.payload["source_url"] == "https://wiki.example.com/pages/12345"
        assert p.payload["file_mtime"] == 1700000000.0


def test_list_files_returns_each_file_once_with_its_mtime(mock_qdrant, mock_embeddings):
    def point(path, mtime):
        p = MagicMock()
        p.payload = {"file_path": path, "file_mtime": mtime}
        return p

    # Two scroll pages; a.md has two chunks.
    mock_qdrant.scroll.side_effect = [
        ([point("a.md", 1.0), point("a.md", 1.0)], "next"),
        ([point("b.md", 2.0)], None),
    ]
    indexer = Indexer(qdrant_client=mock_qdrant, embedding_service=mock_embeddings)

    files = indexer.list_files("mnemos_knowledge", tag="confluence:ENG")

    assert files == {"a.md": 1.0, "b.md": 2.0}
    first_filter = mock_qdrant.scroll.call_args_list[0].kwargs["scroll_filter"]
    assert first_filter.must[0].key == "tags"
    assert mock_qdrant.scroll.call_args_list[1].kwargs["offset"] == "next"


def test_ensure_collection_indexes_facet_fields(mock_qdrant, mock_embeddings):
    """Qdrant facets refuse unindexed fields: the dashboard counts these."""
    indexer = Indexer(qdrant_client=mock_qdrant, embedding_service=mock_embeddings)

    indexer.ensure_collection("mnemos_code")
    indexer.ensure_collection("mnemos_docs")

    indexed = [
        (c.kwargs["collection_name"], c.kwargs["field_name"])
        for c in mock_qdrant.create_payload_index.call_args_list
    ]
    assert indexed == [
        ("mnemos_code", "tags"), ("mnemos_code", "language"), ("mnemos_code", "chunk_type"),
        ("mnemos_docs", "language"), ("mnemos_docs", "chunk_type"),
    ]


def test_ensure_collection_backfills_indexes_on_existing_collections(mock_qdrant, mock_embeddings):
    mock_qdrant.collection_exists.return_value = True
    Indexer(qdrant_client=mock_qdrant, embedding_service=mock_embeddings).ensure_collection("mnemos_skills")

    mock_qdrant.create_collection.assert_not_called()
    fields = [c.kwargs["field_name"] for c in mock_qdrant.create_payload_index.call_args_list]
    assert fields == ["language", "chunk_type"]


def test_ensure_collection_survives_a_replica_creating_it_first(mock_qdrant, mock_embeddings):
    """Two pods starting together both see the collection missing; the loser must not crash."""
    mock_qdrant.collection_exists.side_effect = [False, True]
    mock_qdrant.create_collection.side_effect = RuntimeError("409: already exists")

    Indexer(qdrant_client=mock_qdrant, embedding_service=mock_embeddings).ensure_collection("mnemos_code")

    fields = [c.kwargs["field_name"] for c in mock_qdrant.create_payload_index.call_args_list]
    assert fields == ["tags", "language", "chunk_type"]


def test_ensure_collection_reraises_a_real_creation_failure(mock_qdrant, mock_embeddings):
    mock_qdrant.collection_exists.return_value = False
    mock_qdrant.create_collection.side_effect = RuntimeError("Qdrant down")

    with pytest.raises(RuntimeError, match="Qdrant down"):
        Indexer(qdrant_client=mock_qdrant, embedding_service=mock_embeddings).ensure_collection("mnemos_code")


def test_facet_fields_follow_tagging():
    from core.indexer import facet_fields

    assert facet_fields("mnemos_code") == ("tags", "language", "chunk_type")
    assert facet_fields("mnemos_docs") == ("language", "chunk_type")
