from core.chunkers.go_chunker import GoChunker
from core.chunkers.splitter import split_long_chunks


def _go_function(lines: int) -> str:
    body = "\n".join(f'\tlog.Printf("step %d of the reconciliation loop", {i})' for i in range(lines))
    return f"package svc\n\nfunc Reconcile() {{\n{body}\n}}\n"


def test_short_chunks_pass_through_unchanged():
    chunks = [{"content": "func A() {}", "chunk_index": 0, "chunk_type": "function", "symbol_name": "A"}]
    assert split_long_chunks(chunks, max_chars=100) == chunks


def test_long_function_splits_into_bounded_overlapping_windows_keeping_metadata():
    content = _go_function(200)
    chunks = GoChunker().chunk(content, "svc/reconcile.go")
    big = next(c for c in chunks if c.get("symbol_name") == "Reconcile")
    assert len(big["content"]) > 1500

    out = split_long_chunks(chunks, max_chars=1500, overlap_chars=200)
    parts = [c for c in out if c.get("symbol_name") == "Reconcile"]

    assert len(parts) > 1
    assert all(len(p["content"]) <= 1500 for p in parts)
    # Every line of the function survives the split.
    assert "".join(p["content"] for p in parts).count("reconciliation loop") >= 200
    # Consecutive windows overlap, so a statement cut at a boundary is whole in one of them.
    assert parts[0]["content"].splitlines()[-1] in parts[1]["content"]
    assert {p["chunk_type"] for p in parts} == {big["chunk_type"]}
    assert {p["language"] for p in parts} == {big["language"]}


def test_chunk_index_is_renumbered_contiguously_across_the_file():
    chunks = [
        {"content": "a" * 10, "chunk_index": 0},
        {"content": "line\n" * 100, "chunk_index": 1},
        {"content": "b" * 10, "chunk_index": 2},
    ]
    out = split_long_chunks(chunks, max_chars=120, overlap_chars=20)
    assert [c["chunk_index"] for c in out] == list(range(len(out)))
    assert out[0]["content"] == "a" * 10 and out[-1]["content"] == "b" * 10


def test_a_single_overlong_line_is_cut_at_fixed_width():
    """Minified JSON is one line: the word-based fallback produced 170k-char chunks."""
    out = split_long_chunks([{"content": "x" * 5000, "chunk_index": 0}], max_chars=1500, overlap_chars=200)
    assert [len(c["content"]) for c in out] == [1500, 1500, 1500, 500]


def test_zero_disables_splitting():
    chunks = [{"content": "y" * 10_000, "chunk_index": 0}]
    assert split_long_chunks(chunks, max_chars=0) == chunks
