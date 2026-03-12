from rag_core.chunkers.markdown_chunker import MarkdownChunker


def test_splits_on_h2_headers(sample_markdown):
    chunker = MarkdownChunker()
    chunks = chunker.chunk(sample_markdown, file_path="docs/DDD_PATTERNS.md")
    assert len(chunks) == 3
    assert "## Aggregates" in chunks[0]["content"]
    assert "## Value Objects" in chunks[1]["content"]
    assert "## Repositories" in chunks[2]["content"]


def test_chunk_metadata(sample_markdown):
    chunker = MarkdownChunker()
    chunks = chunker.chunk(sample_markdown, file_path="docs/DDD_PATTERNS.md")
    assert chunks[0]["file_path"] == "docs/DDD_PATTERNS.md"
    assert chunks[0]["chunk_type"] == "section"
    assert chunks[0]["section"] == "Aggregates"


def test_file_without_headers():
    chunker = MarkdownChunker()
    content = "Just plain text without any headers."
    chunks = chunker.chunk(content, file_path="README.md")
    assert len(chunks) == 1
    assert chunks[0]["chunk_type"] == "file"


def test_preserves_h1_as_context(sample_markdown):
    chunker = MarkdownChunker()
    chunks = chunker.chunk(sample_markdown, file_path="docs/DDD_PATTERNS.md")
    assert chunks[0]["doc_title"] == "DDD Patterns"
