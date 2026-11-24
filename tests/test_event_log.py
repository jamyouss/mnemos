from __future__ import annotations

import pytest
from qdrant_client import QdrantClient

from core.event_log import EventLog


@pytest.fixture
def log() -> EventLog:
    log = EventLog(QdrantClient(":memory:"), "events", ("actor", "action"), retention_days=30)
    log.ensure_collection()
    return log


def test_query_pages_newest_first_with_a_cursor(log):
    for i in range(5):
        log.record({"actor": "root", "action": f"a{i}"})

    first, cursor = log.query(limit=2)
    second, cursor2 = log.query(before=cursor, limit=2)
    last, end = log.query(before=cursor2, limit=2)

    assert [e["action"] for e in first + second + last] == ["a4", "a3", "a2", "a1", "a0"]
    assert end is None
    assert first[0]["at"].endswith("+00:00") and first[0]["id"]


def test_records_made_within_one_clock_tick_all_page_through(log, monkeypatch):
    # `before` is exclusive: two records sharing a `ts` across a page boundary
    # would make the second one unreachable.
    monkeypatch.setattr("core.event_log.time.time", lambda: 1_800_000_000.0)
    for i in range(4):
        log.record({"actor": "root", "action": f"a{i}"})

    first, cursor = log.query(limit=2)
    rest, _ = log.query(before=cursor, limit=10)
    assert sorted(e["action"] for e in first + rest) == ["a0", "a1", "a2", "a3"]


def test_filters_match_exact_values_and_lists(log):
    log.record({"actor": "root", "action": "tenant.create"})
    log.record({"actor": "dev", "action": "tenant.create"})
    log.record({"actor": "dev", "action": "projects.update"})

    assert [e["action"] for e in log.query({"actor": "dev"})[0]] == ["projects.update", "tenant.create"]
    assert log.count({"action": "tenant.create"}) == 2
    assert log.count({"actor": ["root", "dev"], "action": "projects.update"}) == 1
    # An empty filter value means "any", as the dashboard sends it.
    assert log.count({"actor": None, "action": ""}) == 3


def test_prune_drops_what_is_past_retention_only(log):
    log.record({"actor": "root", "action": "a"})
    recorded = log.query()[0][0]["ts"]

    log.prune(now=recorded + 29 * 86400)
    assert log.count() == 1
    log.retention_days = 7  # changed live from the Configuration page
    log.prune(now=recorded + 8 * 86400)
    assert log.count() == 0


def test_update_merges_into_a_record(log):
    record_id = log.record({"actor": "root", "action": "job", "processed": 0})
    log.update(record_id, {"processed": 3, "state": "done"})
    stored = log.get(record_id)
    assert stored["processed"] == 3 and stored["state"] == "done" and stored["actor"] == "root"
