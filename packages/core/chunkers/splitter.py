"""Cap chunk size after the language-aware chunkers have run.

The chunkers cut at semantic boundaries (functions, sections, SFC blocks) or
every 500 *words*, so nothing bounds a chunk in characters: a minified JSON
"word" or a long Go function produced chunks of 100k+ characters. The embedder
reads the first ~512 tokens (~1 500 characters of code) and ignores the rest,
which left half of the indexed text unsearchable.
"""
from __future__ import annotations

DEFAULT_MAX_CHARS = 1500
DEFAULT_OVERLAP_CHARS = 200


def split_long_chunks(
    chunks: list[dict],
    max_chars: int = DEFAULT_MAX_CHARS,
    overlap_chars: int = DEFAULT_OVERLAP_CHARS,
) -> list[dict]:
    """Split every chunk longer than `max_chars` into overlapping windows.

    Windows break on line boundaries; a single line longer than `max_chars`
    is cut at fixed width. Each window keeps its chunk's metadata, and
    `chunk_index` is renumbered 0..n-1 across the file so point ids stay
    unique. `max_chars <= 0` disables splitting.
    """
    if max_chars <= 0:
        return chunks
    out: list[dict] = []
    for chunk in chunks:
        content = chunk.get("content", "")
        if len(content) <= max_chars:
            out.append(dict(chunk))
            continue
        out += [{**chunk, "content": w} for w in _windows(content, max_chars, overlap_chars)]
    for i, chunk in enumerate(out):
        chunk["chunk_index"] = i
    return out


def _windows(text: str, max_chars: int, overlap_chars: int) -> list[str]:
    pieces: list[str] = []
    for line in text.splitlines(keepends=True):
        while len(line) > max_chars:
            pieces.append(line[:max_chars])
            line = line[max_chars:]
        if line:
            pieces.append(line)

    windows: list[str] = []
    current: list[str] = []
    size = 0
    for piece in pieces:
        if current and size + len(piece) > max_chars:
            windows.append("".join(current))
            # Carry the tail lines over, as long as the next piece still fits.
            tail: list[str] = []
            tail_size = 0
            for line in reversed(current):
                if tail_size + len(line) > overlap_chars:
                    break
                tail.insert(0, line)
                tail_size += len(line)
            if tail_size + len(piece) > max_chars:
                tail, tail_size = [], 0
            current, size = tail, tail_size
        current.append(piece)
        size += len(piece)
    if current:
        windows.append("".join(current))
    return windows
