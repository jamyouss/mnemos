"""Lightweight BM25 sparse encoder.

Designed to work with Qdrant `Modifier.IDF` so that we only need to compute and
ship term frequencies (TFs) from the client — Qdrant computes the IDF and the
final BM25 score server-side at query time.

The token id space is a 31-bit stable hash; collisions are rare enough at
typical corpus sizes that they do not measurably degrade retrieval quality.
For a code-focused RAG, this approach is competitive with full BM25 libraries
while adding no model download and zero startup cost.
"""
from __future__ import annotations

import hashlib
import re
from collections import Counter

from qdrant_client.models import SparseVector

_TOKEN_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*|\d+")
_CAMEL_RE = re.compile(r"(?<=[a-z])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])")
_MIN_TOKEN_LEN = 2
_HASH_MOD = 2**31

# A pragmatic English stop list — small enough to not hurt code search.
_STOPWORDS: frozenset[str] = frozenset(
    {
        "a", "an", "and", "are", "as", "at", "be", "by", "for", "from",
        "has", "have", "he", "in", "is", "it", "its", "of", "on", "or",
        "that", "the", "this", "to", "was", "were", "will", "with",
        "we", "you", "your", "i",
    }
)


def _split_camel(token: str) -> list[str]:
    """Split CamelCase / snake_case while keeping the original token too.

    "handleRequest"   → ["handleRequest", "handle", "Request"]
    "MyHTTPClient"    → ["MyHTTPClient", "My", "HTTP", "Client"]
    "get_user_by_id"  → ["get_user_by_id", "get", "user", "by", "id"]
    """
    pieces = [token]
    for sub in _CAMEL_RE.split(token):
        for piece in sub.split("_"):
            if piece and piece != token:
                pieces.append(piece)
    return pieces


def tokenize(text: str) -> list[str]:
    raw = _TOKEN_RE.findall(text)
    out: list[str] = []
    for tok in raw:
        for piece in _split_camel(tok):
            lowered = piece.lower()
            if len(lowered) < _MIN_TOKEN_LEN:
                continue
            if lowered in _STOPWORDS:
                continue
            out.append(lowered)
    return out


def _token_id(token: str) -> int:
    """Stable 31-bit hash. Stable across Python invocations (unlike hash())."""
    digest = hashlib.blake2b(token.encode("utf-8"), digest_size=4).digest()
    return int.from_bytes(digest, "big") & 0x7FFFFFFF  # ≤ 2^31 - 1


def bm25_sparse(text: str) -> SparseVector:
    """Encode `text` into a SparseVector of (token_id, tf) pairs."""
    tokens = tokenize(text)
    if not tokens:
        # Qdrant rejects truly-empty sparse vectors; emit a placeholder so
        # ingestion still works even on weird/empty chunks.
        return SparseVector(indices=[0], values=[0.0])

    counts = Counter(tokens)
    indices: list[int] = []
    values: list[float] = []
    # Use a dict to dedup any rare hash collisions across distinct tokens.
    bucketed: dict[int, float] = {}
    for tok, count in counts.items():
        idx = _token_id(tok)
        bucketed[idx] = bucketed.get(idx, 0.0) + float(count)
    for idx, val in bucketed.items():
        indices.append(idx)
        values.append(val)
    return SparseVector(indices=indices, values=values)
