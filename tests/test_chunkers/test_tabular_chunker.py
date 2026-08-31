from core.chunkers.fallback_chunker import FallbackChunker
from core.chunkers.tabular_chunker import TabularChunker

ORDERS = "id,customer,city,status,total\n" + "\n".join(
    f"{i},alice-{i},paris,shipped,{i * 3}.00" for i in range(600)
)


def _chunker(**kw) -> TabularChunker:
    return TabularChunker(fallback=FallbackChunker(), **kw)


def _rows(chunk: dict) -> str:
    """Chunk content minus the leading `columns: ...` header line."""
    return chunk["content"].split("\n\n", 1)[1]


# ---------------------------------------------------------------------------
# The reason this chunker exists
# ---------------------------------------------------------------------------


def test_every_chunk_carries_the_column_names():
    """The whole point: the fallback chunker leaves the header in chunk 0 only,
    so a query naming a column cannot match any later chunk."""
    chunks = _chunker().chunk(ORDERS, "orders.csv")
    assert len(chunks) > 1, "600 rows must not collapse into a single chunk"
    for c in chunks:
        for column in ("id", "customer", "city", "status", "total"):
            assert column in c["content"]


def test_columns_survive_even_when_every_cell_is_empty():
    """Real exports are sparse. Empty cells are skipped when rendering a row,
    so without the header line a column could be missing from a whole chunk —
    which is exactly the failure this chunker exists to prevent."""
    content = "id,city,status\n1,,\n2,,\n3,,\n"
    chunks = _chunker().chunk(content, "sparse.csv")
    assert chunks
    for c in chunks:
        assert "city" in c["content"]
        assert "status" in c["content"]
        # …but no dangling "city: " with nothing after it.
        assert "city: " not in _rows(c)


def test_fallback_chunker_loses_the_header_after_the_first_chunk():
    """Pins the behaviour being fixed, so a regression is visible here."""
    chunks = FallbackChunker().chunk(ORDERS, "orders.csv")
    assert "customer" in chunks[0]["content"]
    assert all("customer" not in c["content"] for c in chunks[1:])


def test_rows_render_as_column_value_pairs():
    chunks = _chunker().chunk("id,city\n7,paris\n", "x.csv")
    assert _rows(chunks[0]) == "id: 7 | city: paris"


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------


def test_quoted_field_containing_the_delimiter_survives():
    content = 'id,label,city\n1,"Paris, France",paris\n'
    chunks = _chunker().chunk(content, "x.csv")
    assert "label: Paris, France" in chunks[0]["content"]


def test_quoted_field_containing_a_newline_survives():
    content = 'id,note\n1,"line one\nline two"\n'
    chunks = _chunker().chunk(content, "x.csv")
    assert len(chunks) == 1
    assert "line one\nline two" in chunks[0]["content"]


def test_tsv_uses_tab_delimiter():
    chunks = _chunker().chunk("id\tcity\n7\tparis\n", "x.tsv")
    assert _rows(chunks[0]) == "id: 7 | city: paris"
    assert chunks[0]["language"] == "tsv"


def test_semicolon_delimiter_is_sniffed():
    chunks = _chunker().chunk("id;city;status\n7;paris;shipped\n", "x.csv")
    assert _rows(chunks[0]) == "id: 7 | city: paris | status: shipped"


# ---------------------------------------------------------------------------
# Degenerate inputs fall back rather than dropping the file
# ---------------------------------------------------------------------------


def test_empty_content_yields_nothing():
    assert _chunker().chunk("", "x.csv") == []
    assert _chunker().chunk("   \n  ", "x.csv") == []


def test_single_column_falls_back():
    """One column has no column/value structure worth rendering, but the text
    is still text — it must not vanish from the index."""
    content = "notes\n" + "\n".join(f"line {i}" for i in range(50))
    chunks = _chunker().chunk(content, "x.csv")
    assert chunks
    assert chunks[0]["chunk_type"] != "rows"


def test_header_only_file_falls_back():
    chunks = _chunker().chunk("id,customer,city\n", "x.csv")
    assert chunks
    assert chunks[0]["chunk_type"] != "rows"


def test_prose_misnamed_as_csv_falls_back():
    content = "This file is not a table at all.\nIt is just prose.\n"
    chunks = _chunker().chunk(content, "x.csv")
    assert chunks
    assert chunks[0]["chunk_type"] != "rows"


def test_without_a_fallback_degenerate_input_yields_nothing():
    assert TabularChunker().chunk("id,customer,city\n", "x.csv") == []


# ---------------------------------------------------------------------------
# Ragged rows must never raise
# ---------------------------------------------------------------------------


def test_short_row_stops_early():
    chunks = _chunker().chunk("id,city,status\n7,paris\n", "x.csv")
    assert _rows(chunks[0]) == "id: 7 | city: paris"


def test_extra_cells_beyond_the_header_are_dropped():
    chunks = _chunker().chunk("id,city\n7,paris,extra,more\n", "x.csv")
    assert _rows(chunks[0]) == "id: 7 | city: paris"


def test_empty_cells_are_skipped():
    chunks = _chunker().chunk("id,city,status\n7,,shipped\n", "x.csv")
    assert _rows(chunks[0]) == "id: 7 | status: shipped"


def test_fully_empty_row_produces_no_text():
    chunks = _chunker().chunk("id,city\n7,paris\n,\n8,lyon\n", "x.csv")
    joined = "\n".join(c["content"] for c in chunks)
    assert "id: 7 | city: paris" in joined
    assert "id: 8 | city: lyon" in joined


# ---------------------------------------------------------------------------
# Sizing
# ---------------------------------------------------------------------------


def test_rows_are_grouped_up_to_the_char_budget():
    chunks = _chunker(char_budget=120).chunk(ORDERS, "orders.csv")
    assert len(chunks) > 10
    # A chunk overshoots by at most the one row that crossed the budget.
    longest_row = max(len(r) for r in chunks[0]["content"].split("\n\n"))
    assert all(len(c["content"]) <= 120 + longest_row + 2 for c in chunks)


def test_max_chunks_caps_a_huge_export():
    chunks = _chunker(char_budget=60, max_chunks=5).chunk(ORDERS, "orders.csv")
    assert len(chunks) == 5


def test_chunk_metadata_contract():
    """chunk_type / symbol_name / language / chunk_index are required by the
    indexer payload contract (see CLAUDE.md)."""
    chunks = _chunker(char_budget=100).chunk(ORDERS, "orders.csv")
    for i, c in enumerate(chunks):
        assert c["chunk_type"] == "rows"
        assert c["symbol_name"] == ""
        assert c["language"] == "csv"
        assert c["chunk_index"] == i
        assert c["file_path"] == "orders.csv"
        assert c["columns"] == ["id", "customer", "city", "status", "total"]
