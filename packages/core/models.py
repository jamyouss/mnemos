from __future__ import annotations

from pydantic import BaseModel


class SearchResult(BaseModel):
    content: str
    file_path: str
    score: float
    chunk_type: str
    collection: str
    metadata: dict


class CodeSearchResult(SearchResult):
    language: str
    symbol_name: str | None = None
    package: str | None = None


class SkillResult(BaseModel):
    skill_name: str
    description: str
    score: float
    instructions_preview: str


class MemoryEntry(BaseModel):
    id: str
    content: str
    project: str | None = None
    topic: str | None = None
    memory_type: str
    tags: list[str] = []
    status: str = "pending"
    created_at: str


class MemoryResult(BaseModel):
    id: str
    content: str
    project: str | None = None
    topic: str | None = None
    memory_type: str
    tags: list[str] = []
    score: float
    created_at: str


class ReindexStatus(BaseModel):
    collection: str | None = None
    mode: str
    files_processed: int
    files_added: int
    files_updated: int
    files_deleted: int
    duration_ms: int


class CollectionStatus(BaseModel):
    name: str
    document_count: int
    last_indexed_at: str | None = None
    stale_files: int = 0


class StatusReport(BaseModel):
    collections: dict[str, CollectionStatus]
    qdrant_healthy: bool
    watcher_active: bool = False


class ExtractedMemory(BaseModel):
    content: str
    memory_type: str = "note"  # decision, pattern, lesson, convention, note
    project: str | None = None
    tags: list[str] = []


class DeduplicationResult(BaseModel):
    action: str  # "inserted", "merged", "replaced"
    memory_id: str
    merged_with: str | None = None
