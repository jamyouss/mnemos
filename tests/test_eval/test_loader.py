from __future__ import annotations

from pathlib import Path

from mnemos_eval.loader import (
    load_candidates,
    load_golden,
    promote_candidates,
    save_candidates,
    save_golden,
)
from mnemos_eval.schema import GoldenCandidate, GoldenItem


def test_save_and_load_golden(tmp_path: Path):
    path = tmp_path / "golden.yaml"
    items = [
        GoldenItem(
            id="q1",
            query="how does X work?",
            intent="code_search",
            expected_files=["foo/bar.py"],
            k_relevant=1,
        )
    ]
    save_golden(path, items)
    loaded = load_golden(path)
    assert len(loaded) == 1
    assert loaded[0].id == "q1"
    assert loaded[0].expected_files == ["foo/bar.py"]


def test_load_golden_missing_returns_empty(tmp_path: Path):
    assert load_golden(tmp_path / "nope.yaml") == []


def test_promote_candidates_moves_accepted(tmp_path: Path):
    candidates_path = tmp_path / "_candidates.yaml"
    golden_path = tmp_path / "golden.yaml"

    candidates = [
        GoldenCandidate(
            id="c1",
            query="q1",
            intent="code_search",
            suggested_files=["a.py"],
            source_collection="mnemos_code_moby",
            reviewed=True,
            accepted=True,
        ),
        GoldenCandidate(
            id="c2",
            query="q2",
            intent="code_search",
            suggested_files=["b.py"],
            source_collection="mnemos_code_moby",
            reviewed=True,
            accepted=False,
        ),
        GoldenCandidate(
            id="c3",
            query="q3",
            intent="code_search",
            suggested_files=["c.py"],
            source_collection="mnemos_code_moby",
            reviewed=False,
            accepted=False,
        ),
    ]
    save_candidates(candidates_path, candidates)

    promoted, remaining = promote_candidates(candidates_path, golden_path)

    assert promoted == 1
    assert remaining == 1  # c3 is still un-reviewed; c2 was reviewed-and-rejected → dropped

    golden = load_golden(golden_path)
    assert len(golden) == 1
    assert golden[0].id == "c1"

    leftover = load_candidates(candidates_path)
    assert {c.id for c in leftover} == {"c3"}


def test_promote_skips_duplicates(tmp_path: Path):
    candidates_path = tmp_path / "_candidates.yaml"
    golden_path = tmp_path / "golden.yaml"

    save_golden(
        golden_path,
        [GoldenItem(id="dup", query="existing", expected_files=["x.py"])],
    )
    save_candidates(
        candidates_path,
        [
            GoldenCandidate(
                id="dup",
                query="dup query",
                intent="code_search",
                suggested_files=["y.py"],
                source_collection="mnemos_code_moby",
                reviewed=True,
                accepted=True,
            )
        ],
    )

    promoted, _ = promote_candidates(candidates_path, golden_path)
    assert promoted == 0
    assert len(load_golden(golden_path)) == 1
