from __future__ import annotations

import math
from unittest.mock import MagicMock

from core.collections import CollectionConfig
from core.router import QueryRouter, _cosine


def _unit(v):
    n = math.sqrt(sum(x * x for x in v))
    return [x / n for x in v] if n else v


def _make_embed_service(query_vec, description_vecs):
    """Return a MagicMock whose .embed and .embed_batch return canned vectors."""
    mock = MagicMock()
    mock.embed.return_value = query_vec
    mock.embed_batch.return_value = description_vecs
    return mock


def test_router_disabled_returns_all_collections():
    configs = [
        CollectionConfig(name="a", description="apples"),
        CollectionConfig(name="b", description="bananas"),
    ]
    mock = _make_embed_service([1.0, 0.0], [[1.0, 0.0], [0.0, 1.0]])
    r = QueryRouter(embedding_service=mock, enabled=False, collections=configs)
    out = r.route("anything")
    assert [c.name for c in out] == ["a", "b"]


def test_router_picks_most_similar_top_k():
    configs = [
        CollectionConfig(name="skills", description="agent skills"),
        CollectionConfig(name="docs", description="documentation pages"),
        CollectionConfig(name="code", description="source code"),
    ]
    # Query embedding matches "skills" most, then "docs"
    q_vec = _unit([1.0, 0.1, 0.0])
    desc_vecs = [_unit([1.0, 0.0, 0.0]), _unit([0.5, 0.5, 0.0]), _unit([0.0, 0.0, 1.0])]
    mock = _make_embed_service(q_vec, desc_vecs)
    r = QueryRouter(embedding_service=mock, enabled=True, top_k=2, collections=configs)
    out = r.route("how to use agent skill X")
    assert [c.name for c in out] == ["skills", "docs"]


def test_router_falls_back_when_top_score_below_threshold():
    configs = [
        CollectionConfig(name="a", description="apples"),
        CollectionConfig(name="b", description="bananas"),
    ]
    q_vec = _unit([1.0, 0.0])
    # Both descriptions are orthogonal to the query → low cosine
    desc_vecs = [_unit([0.0, 1.0]), _unit([0.0, 1.0])]
    mock = _make_embed_service(q_vec, desc_vecs)
    r = QueryRouter(embedding_service=mock, enabled=True, top_k=1, min_score=0.8, collections=configs)
    out = r.route("nothing matches")
    # Top score is 0 < 0.8 → fallback to all collections
    assert {c.name for c in out} == {"a", "b"}


def test_router_respects_allowed_subset():
    configs = [
        CollectionConfig(name="a", description="apples"),
        CollectionConfig(name="b", description="bananas"),
        CollectionConfig(name="c", description="cherries"),
    ]
    q_vec = _unit([1.0, 0.0, 0.0])
    desc_vecs = [_unit([1.0, 0.0, 0.0]), _unit([0.0, 1.0, 0.0]), _unit([0.0, 0.0, 1.0])]
    mock = _make_embed_service(q_vec, desc_vecs)
    r = QueryRouter(embedding_service=mock, enabled=True, top_k=2, collections=configs)
    out = r.route("apple pie", allowed=["b", "c"])
    # "a" is excluded even though it would otherwise win
    assert set(c.name for c in out) <= {"b", "c"}


def test_router_skips_collections_without_description():
    configs = [
        CollectionConfig(name="with-desc", description="something"),
        CollectionConfig(name="no-desc", description=""),
    ]
    mock = _make_embed_service([1.0, 0.0], [[1.0, 0.0]])  # only 1 desc embedded
    r = QueryRouter(embedding_service=mock, enabled=True, top_k=5, collections=configs)
    out = r.route("q")
    assert [c.name for c in out] == ["with-desc"]


def test_cosine_helper_matches_numpy_for_normalised_vectors():
    a = _unit([3.0, 4.0])
    b = _unit([4.0, 3.0])
    # 3*4 + 4*3 = 24, divided by 5*5 = 25 → 0.96
    assert _cosine(a, b) == 0.96
