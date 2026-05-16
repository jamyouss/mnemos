from core.chunkers.fallback_chunker import FallbackChunker


def test_small_file_returns_single_chunk():
    chunker = FallbackChunker(max_tokens=500, overlap_tokens=50)
    content = "short file content"
    chunks = chunker.chunk(content, file_path="small.txt")
    assert len(chunks) == 1
    assert chunks[0]["content"] == content
    assert chunks[0]["file_path"] == "small.txt"
    assert chunks[0]["chunk_type"] == "file"


def test_large_file_splits_into_overlapping_chunks():
    chunker = FallbackChunker(max_tokens=10, overlap_tokens=2)
    words = [f"word{i}" for i in range(30)]
    content = " ".join(words)
    chunks = chunker.chunk(content, file_path="large.txt")
    assert len(chunks) > 1
    assert chunks[0]["chunk_type"] == "window"
    for i in range(len(chunks) - 1):
        chunk_words = chunks[i]["content"].split()
        next_words = chunks[i + 1]["content"].split()
        assert chunk_words[-1] in next_words or chunk_words[-2] in next_words


def test_chunk_metadata():
    chunker = FallbackChunker(max_tokens=500, overlap_tokens=50)
    chunks = chunker.chunk("content", file_path="path/to/file.go")
    assert chunks[0]["file_path"] == "path/to/file.go"
    assert "chunk_index" in chunks[0]
