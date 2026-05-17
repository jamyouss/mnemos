from core.collections import COLLECTIONS, CollectionConfig, get_collection_for_path


def test_all_collections_defined():
    names = {c.name for c in COLLECTIONS}
    assert names == {
        "mnemos_skills",
        "mnemos_docs",
        "mnemos_code",      # single, project-scoped via payload
        "mnemos_memory",
    }


def test_collection_config_has_vector_size():
    for c in COLLECTIONS:
        assert c.vector_size == 384


def test_path_to_collection_mapping_for_code():
    """Anything that looks like a real relative path falls into mnemos_code."""
    assert get_collection_for_path("myproject/services/core/main.go") == "mnemos_code"
    assert get_collection_for_path("otherproject/go-modules/ddd/entity.go") == "mnemos_code"
    assert get_collection_for_path("infra/docker/compose.yml") == "mnemos_code"


def test_path_to_collection_skills_and_docs():
    assert get_collection_for_path("skills/project-expert/instructions.md") == "mnemos_skills"
    assert get_collection_for_path("docs/DDD_PATTERNS.md") == "mnemos_docs"


def test_absolute_path_returns_none():
    assert get_collection_for_path("/absolute/path/file.go") is None


def test_empty_string_returns_none():
    assert get_collection_for_path("") is None
