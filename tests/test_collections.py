from rag_core.collections import COLLECTIONS, CollectionConfig, get_collection_for_path


def test_all_collections_defined():
    names = {c.name for c in COLLECTIONS}
    assert names == {
        "rag_skills",
        "rag_docs",
        "rag_memory",
        "rag_code_moby",
        "rag_code_trevio",
        "rag_code_infra",
    }


def test_collection_config_has_vector_size():
    for c in COLLECTIONS:
        assert c.vector_size == 384


def test_path_to_collection_mapping():
    assert get_collection_for_path("moby/services/core/main.go") == "rag_code_moby"
    assert get_collection_for_path("trevio/go-modules/ddd/entity.go") == "rag_code_trevio"
    assert get_collection_for_path("infra/docker/compose.yml") == "rag_code_infra"
    assert get_collection_for_path("github-cicd/workflow.yml") == "rag_code_infra"


def test_path_to_collection_skills():
    assert get_collection_for_path("skills/moby-expert/instructions.md") == "rag_skills"
    assert get_collection_for_path("docs/DDD_PATTERNS.md") == "rag_docs"


def test_unknown_path_returns_none():
    assert get_collection_for_path("unknown/random/file.txt") is None
