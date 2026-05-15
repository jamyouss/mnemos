from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from rag_core.observability import QueryLogger


def _read(path: Path) -> list[dict]:
    with path.open("r", encoding="utf-8") as fh:
        return [json.loads(line) for line in fh if line.strip()]


def test_logger_disabled_writes_nothing(tmp_path: Path):
    path = tmp_path / "log.jsonl"
    q = QueryLogger(path, enabled=False)
    q.log("hello", [], 12.3)
    assert not path.exists()


def test_logger_appends_one_entry(tmp_path: Path):
    path = tmp_path / "log.jsonl"
    q = QueryLogger(path, enabled=True)
    results = [SimpleNamespace(file_path="a.py", score=0.91)]
    q.log("hello", results, 42.5, intent="search", tenant="t1")
    entries = _read(path)
    assert len(entries) == 1
    e = entries[0]
    assert e["query"] == "hello"
    assert e["top_files"] == ["a.py"]
    assert e["top_scores"] == [0.91]
    assert e["latency_ms"] == 42.5
    assert e["intent"] == "search"
    assert e["tenant"] == "t1"
    assert e["n_results"] == 1
    assert "ts" in e


def test_logger_accepts_dict_results(tmp_path: Path):
    path = tmp_path / "log.jsonl"
    q = QueryLogger(path, enabled=True)
    q.log("q", [{"file_path": "x", "score": 0.5}], 1.0)
    entries = _read(path)
    assert entries[0]["top_files"] == ["x"]
    assert entries[0]["top_scores"] == [0.5]


def test_logger_truncates_to_top_10(tmp_path: Path):
    path = tmp_path / "log.jsonl"
    q = QueryLogger(path, enabled=True)
    many = [SimpleNamespace(file_path=f"f{i}", score=float(i)) for i in range(50)]
    q.log("q", many, 1.0)
    entries = _read(path)
    assert len(entries[0]["top_files"]) == 10
    assert entries[0]["n_results"] == 50


def test_logger_appends_multiple_calls(tmp_path: Path):
    path = tmp_path / "log.jsonl"
    q = QueryLogger(path, enabled=True)
    q.log("a", [], 1.0)
    q.log("b", [], 2.0)
    entries = _read(path)
    assert [e["query"] for e in entries] == ["a", "b"]


def test_logger_extra_fields_are_merged(tmp_path: Path):
    path = tmp_path / "log.jsonl"
    q = QueryLogger(path, enabled=True)
    q.log("q", [], 1.0, extra={"variant": "rerank_on", "cache_hit": False})
    entries = _read(path)
    assert entries[0]["variant"] == "rerank_on"
    assert entries[0]["cache_hit"] is False
