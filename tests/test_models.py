from core.models import (
    SearchResult,
    CodeSearchResult,
    SkillResult,
    MemoryEntry,
    MemoryResult,
    ReindexStatus,
    StatusReport,
    CollectionStatus,
)


def test_search_result_to_dict():
    result = SearchResult(
        content="func Create()",
        file_path="myproject/services/core/app.go",
        score=0.87,
        chunk_type="function",
        collection="mnemos_code_myproject",
        metadata={"package": "application"},
    )
    d = result.model_dump()
    assert d["content"] == "func Create()"
    assert d["score"] == 0.87
    assert d["collection"] == "mnemos_code_myproject"


def test_code_search_result_extends_search_result():
    result = CodeSearchResult(
        content="func Create()",
        file_path="myproject/services/core/app.go",
        score=0.87,
        chunk_type="function",
        collection="mnemos_code_myproject",
        metadata={"package": "application"},
        language="go",
        symbol_name="Create",
        package="application",
    )
    assert result.language == "go"
    assert result.symbol_name == "Create"
    assert isinstance(result, SearchResult)


def test_memory_entry_defaults():
    entry = MemoryEntry(
        id="mem_123",
        content="Always use flat routes",
        project="myproject",
        topic="routing",
        memory_type="preference",
        tags=["api", "routing"],
        status="approved",
        created_at="2026-03-12T14:30:00Z",
    )
    assert entry.status == "approved"
    assert entry.memory_type == "preference"


def test_skill_result():
    result = SkillResult(
        skill_name="project-expert",
        description="Expert on Moby project",
        score=0.92,
        instructions_preview="Expert on the example project...",
    )
    assert result.skill_name == "project-expert"


def test_reindex_status():
    status = ReindexStatus(
        collection="mnemos_code_myproject",
        mode="incremental",
        files_processed=42,
        files_added=5,
        files_updated=10,
        files_deleted=2,
        duration_ms=1500,
    )
    assert status.files_processed == 42
    assert status.mode == "incremental"


def test_status_report():
    report = StatusReport(
        collections={
            "mnemos_code_myproject": CollectionStatus(
                name="mnemos_code_myproject",
                document_count=1500,
                last_indexed_at="2026-03-12T10:00:00Z",
                stale_files=3,
            )
        },
        qdrant_healthy=True,
        watcher_active=True,
    )
    assert report.qdrant_healthy is True
    assert report.collections["mnemos_code_myproject"].document_count == 1500


def test_memory_result():
    result = MemoryResult(
        id="mem_123",
        content="Fixed NATS reconnection",
        project="myproject",
        topic="nats",
        memory_type="bug-fix",
        tags=["nats"],
        score=0.85,
        created_at="2026-03-12T14:30:00Z",
    )
    assert result.score == 0.85
    assert result.memory_type == "bug-fix"
