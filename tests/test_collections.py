from rag_core.collections import COLLECTIONS, CollectionConfig, get_collection_for_path


def test_all_collections_defined():
    names = {c.name for c in COLLECTIONS}
    assert names == {
        "mnemos_skills",
        "mnemos_docs",
        "mnemos_memory",
        "mnemos_code_moby",
        "mnemos_code_trevio",
        "mnemos_code_infra",
    }


def test_collection_config_has_vector_size():
    for c in COLLECTIONS:
        assert c.vector_size == 384


def test_path_to_collection_mapping():
    assert get_collection_for_path("moby/services/core/main.go") == "mnemos_code_moby"
    assert get_collection_for_path("trevio/go-modules/ddd/entity.go") == "mnemos_code_trevio"
    assert get_collection_for_path("infra/docker/compose.yml") == "mnemos_code_infra"
    assert get_collection_for_path("github-cicd/workflow.yml") == "mnemos_code_infra"


def test_path_to_collection_skills():
    assert get_collection_for_path("skills/moby-expert/instructions.md") == "mnemos_skills"
    assert get_collection_for_path("docs/DDD_PATTERNS.md") == "mnemos_docs"


def test_unknown_path_returns_none():
    assert get_collection_for_path("unknown/random/file.txt") is None
