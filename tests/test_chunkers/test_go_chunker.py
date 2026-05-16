from core.chunkers.go_chunker import GoChunker


def test_extracts_functions(sample_go_code):
    chunker = GoChunker()
    chunks = chunker.chunk(sample_go_code, file_path="service.go")
    func_chunks = [c for c in chunks if c["chunk_type"] == "function"]
    assert len(func_chunks) == 2
    names = {c["symbol_name"] for c in func_chunks}
    assert names == {"Create", "Get"}


def test_extracts_type_declarations(sample_go_code):
    chunker = GoChunker()
    chunks = chunker.chunk(sample_go_code, file_path="service.go")
    type_chunks = [c for c in chunks if c["chunk_type"] == "type"]
    assert len(type_chunks) == 1
    assert type_chunks[0]["symbol_name"] == "CompanyService"


def test_extracts_file_header(sample_go_code):
    chunker = GoChunker()
    chunks = chunker.chunk(sample_go_code, file_path="service.go")
    header_chunks = [c for c in chunks if c["chunk_type"] == "header"]
    assert len(header_chunks) == 1
    assert "package application" in header_chunks[0]["content"]
    assert "import" in header_chunks[0]["content"]


def test_chunk_metadata(sample_go_code):
    chunker = GoChunker()
    chunks = chunker.chunk(sample_go_code, file_path="moby/services/core/app.go")
    for chunk in chunks:
        assert chunk["file_path"] == "moby/services/core/app.go"
        assert chunk["language"] == "go"


def test_fallback_on_parse_error():
    chunker = GoChunker()
    invalid_go = "this is not valid go code at all {{{}}"
    chunks = chunker.chunk(invalid_go, file_path="bad.go")
    assert len(chunks) >= 1
    assert chunks[0]["file_path"] == "bad.go"
