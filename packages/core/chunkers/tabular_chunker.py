"""Chunker for delimited tabular files (``.csv`` / ``.tsv``).

The fallback chunker splits on whitespace, which destroys a table: rows get
glued together and the header survives only in chunk 0. Every later chunk is
a bag of comma-joined values with no column names in it at all, so neither
BM25 nor the dense vector can match a query naming a column.

This chunker keeps each row readable on its own by rendering it as
``column: value`` pairs, so the column names ride along in every chunk::

    id: 449 | customer: alice-449 | city: paris
    status: shipped | total: 1347.00

Rows are grouped up to a character budget rather than a fixed row count, so a
wide table produces fewer rows per chunk instead of oversized ones.
"""
from __future__ import annotations

import csv
import io

# Chunk size target. Rows are added until the next one would cross it, so a
# chunk overshoots by at most one row.
DEFAULT_CHAR_BUDGET = 1500

# Rendering separators.
_PAIR_SEP = " | "
_ROW_SEP = "\n\n"

# csv.Sniffer only needs a taste of the file, and feeding it a huge string is
# both slow and more likely to confuse it.
_SNIFF_BYTES = 8192

# Upper bound on chunks emitted for one file, so a million-row export cannot
# flood the collection. The tail is dropped rather than truncating a row.
DEFAULT_MAX_CHUNKS = 200


class TabularChunker:
    def __init__(
        self,
        char_budget: int = DEFAULT_CHAR_BUDGET,
        max_chunks: int = DEFAULT_MAX_CHUNKS,
        fallback=None,
    ) -> None:
        self.char_budget = char_budget
        self.max_chunks = max_chunks
        # A .csv extension is a hint, not a guarantee. When the content turns
        # out not to be a table, delegating beats returning nothing: the file
        # is still text somebody may want to find.
        self._fallback = fallback

    def chunk(self, content: str, file_path: str) -> list[dict]:
        if not content.strip():
            return []

        language = "tsv" if file_path.endswith(".tsv") else "csv"

        parsed = self._parse(content, language)
        if parsed is None:
            return self._delegate(content, file_path)
        header, rows = parsed

        # A single column carries no column/value structure worth rendering,
        # and a header-only file has no rows to pair with it.
        if len(header) < 2 or not rows:
            return self._delegate(content, file_path)

        chunks: list[dict] = []
        buffer: list[str] = []
        size = 0

        def flush() -> None:
            nonlocal buffer, size
            if not buffer:
                return
            chunks.append(
                {
                    "content": _ROW_SEP.join(buffer),
                    "file_path": file_path,
                    "chunk_type": "rows",
                    "symbol_name": "",
                    "language": language,
                    "chunk_index": len(chunks),
                    "columns": list(header),
                }
            )
            buffer = []
            size = 0

        for row in rows:
            rendered = self._render_row(header, row)
            if not rendered:
                continue
            if buffer and size + len(rendered) > self.char_budget:
                flush()
                if len(chunks) >= self.max_chunks:
                    return chunks
            buffer.append(rendered)
            size += len(rendered) + len(_ROW_SEP)

        flush()
        return chunks[: self.max_chunks]

    # -- internals ---------------------------------------------------------

    def _delegate(self, content: str, file_path: str) -> list[dict]:
        if self._fallback is None:
            return []
        return self._fallback.chunk(content, file_path)

    def _parse(self, content: str, language: str) -> tuple[list[str], list[list[str]]] | None:
        """Return ``(header, rows)``, or None when the file will not parse.

        Uses the stdlib reader rather than ``str.split`` so quoted fields
        containing the delimiter or a newline survive intact.
        """
        delimiter = self._sniff(content, language)
        try:
            reader = csv.reader(io.StringIO(content), delimiter=delimiter)
            header = next(reader, None)
            if not header:
                return None
            rows = list(reader)
        except (csv.Error, UnicodeDecodeError):
            return None

        header = [h.strip() for h in header]
        # A header whose cells are all empty is not a header.
        if not any(header):
            return None
        return header, rows

    @staticmethod
    def _sniff(content: str, language: str) -> str:
        default = "\t" if language == "tsv" else ","
        try:
            dialect = csv.Sniffer().sniff(content[:_SNIFF_BYTES], delimiters=",;\t|")
            return dialect.delimiter
        except csv.Error:
            return default

    @staticmethod
    def _render_row(header: list[str], row: list[str]) -> str:
        """Render one row as ``column: value`` pairs.

        Ragged rows are tolerated: a short row simply stops early, and extra
        cells beyond the header are dropped. Empty cells are skipped so the
        text stays about the values that exist.
        """
        pairs = []
        for column, value in zip(header, row):
            value = value.strip()
            if not column or not value:
                continue
            pairs.append(f"{column}: {value}")
        return _PAIR_SEP.join(pairs)
