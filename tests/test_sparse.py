from __future__ import annotations

from rag_core.sparse import bm25_sparse, tokenize


def test_tokenize_basic_words():
    assert tokenize("Hello, World!") == ["hello", "world"]


def test_tokenize_filters_stopwords():
    tokens = tokenize("the quick brown fox is on the table")
    assert "the" not in tokens
    assert "on" not in tokens
    assert "quick" in tokens
    assert "brown" in tokens


def test_tokenize_splits_camel_case():
    tokens = tokenize("handleHTTPRequest getUserById")
    # Original tokens are kept too — useful for exact-match retrieval
    assert "handlehttprequest" in tokens
    assert "handle" in tokens
    assert "request" in tokens
    assert "getuserbyid" in tokens
    assert "user" in tokens


def test_tokenize_splits_snake_case():
    tokens = tokenize("get_user_by_id")
    assert "get_user_by_id" in tokens
    assert "user" in tokens


def test_tokenize_drops_short_tokens():
    # 'a' and 'i' are below min length AND stopwords, so neither makes it through
    assert "a" not in tokenize("a quick fox")


def test_bm25_sparse_returns_indices_and_values():
    sv = bm25_sparse("the quick brown fox quick brown")
    # Both lists same length, no negative indices, TFs > 0
    assert len(sv.indices) == len(sv.values)
    assert all(i >= 0 for i in sv.indices)
    assert all(v > 0 for v in sv.values)
    # "quick" and "brown" appear twice, "fox" once → some value is 2.0
    assert 2.0 in sv.values


def test_bm25_sparse_empty_text_returns_placeholder():
    sv = bm25_sparse("")
    # Qdrant rejects empty sparse vectors — we emit a single zero-valued bucket.
    assert sv.indices == [0]
    assert sv.values == [0.0]


def test_bm25_sparse_only_stopwords_returns_placeholder():
    sv = bm25_sparse("the the is on")
    assert sv.indices == [0]
    assert sv.values == [0.0]


def test_bm25_sparse_is_deterministic():
    a = bm25_sparse("Service Repository handler")
    b = bm25_sparse("Service Repository handler")
    assert sorted(zip(a.indices, a.values)) == sorted(zip(b.indices, b.values))


def test_tokenize_handles_numeric_only_tokens():
    tokens = tokenize("HTTP 200 OK 404 NotFound")
    # Numbers are preserved
    assert "200" in tokens
    assert "404" in tokens
