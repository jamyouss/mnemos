from __future__ import annotations

import time
from unittest.mock import MagicMock

import pytest
from qdrant_client import QdrantClient

from core.event_log import EventLog
from server.api import run_reindex
from server.indexing import INDEX_EVENT_FIELDS, ReindexJob
from tests.config_fakes import InMemoryConfigQdrant, boot, isolate_settings


def _log() -> EventLog:
    log = EventLog(QdrantClient(":memory:"), "events", INDEX_EVENT_FIELDS, retention_days=30)
    log.ensure_collection()
    return log


def test_a_bulk_reindex_is_followed_from_start_to_end(tmp_path):
    (tmp_path / "ok.py").write_text("print(1)\n")
    (tmp_path / "broken.py").write_text("x\n")
    (tmp_path / "empty.py").write_text("")
    indexer = MagicMock()
    indexer.should_skip.return_value = False

    def index_file(content, file_path, **_):
        if file_path.endswith("broken.py"):
            raise RuntimeError("embedding service down")
        return 2 if content else 0

    indexer.index_file.side_effect = index_file
    log = _log()
    job = ReindexJob(log, collection="mnemos_code", path=str(tmp_path), full=True, actor="root", channel="cli")

    run_reindex(indexer, "mnemos_code", tmp_path, True, job=job)

    stored = log.get(job.id)
    # A walk that ended with failed files is not "done": the page flags it.
    assert stored["state"] == "partial" and stored["finished_at"]
    assert {k: stored[k] for k in ("total", "processed", "chunks", "failed", "empty")} == \
        {"total": 3, "processed": 3, "chunks": 2, "failed": 1, "empty": 1}
    assert stored["errors"][0]["file_path"].endswith("broken.py")
    assert "embedding service down" in stored["errors"][0]["error"]
    # The failure is also an event of its own, tied to the job; the successes are not.
    failed, _ = log.query({"kind": "file"})
    assert [(e["op"], e["job_id"]) for e in failed] == [("failed", job.id)]


def test_a_reindex_that_cannot_list_its_files_ends_failed(tmp_path):
    indexer = MagicMock()
    indexer.should_skip.side_effect = PermissionError("mount gone")
    (tmp_path / "a.py").write_text("x")
    log = _log()
    job = ReindexJob(log, collection="mnemos_code", path=str(tmp_path), full=True, actor="root", channel="api")

    with pytest.raises(PermissionError):
        run_reindex(indexer, "mnemos_code", tmp_path, True, job=job)
    assert log.get(job.id)["state"] == "failed" and "mount gone" in log.get(job.id)["error"]


@pytest.fixture
def client(tmp_path, monkeypatch):
    isolate_settings(monkeypatch, tmp_path)
    monkeypatch.setattr("server.api._ALLOWED_BASE_DIRS", [str(tmp_path)])
    with boot(monkeypatch, InMemoryConfigQdrant()) as client:
        indexer = MagicMock()
        indexer.index_file.return_value = 3
        indexer.should_skip.return_value = False
        client.app.state.indexer = indexer
        client.app.state.index_events = _log()
        yield client


def test_pushes_and_deletes_are_recorded_with_their_channel(client, tmp_path):
    client.post("/api/index", headers={"X-Mnemos-Client": "hook"}, json={
        "file_path": "/data/codebase/acme/api/main.go", "collection": "mnemos_code", "content": "package main\n",
    })
    client.post("/internal/reindex", json={
        "file_path": str(tmp_path / "gone.go"), "collection": "mnemos_code", "event": "deleted",
    })

    events = client.get("/api/indexing/events").json()["items"]
    assert [(e["op"], e["channel"], e["chunks"]) for e in events] == [("deleted", "watcher", 0), ("indexed", "hook", 3)]
    assert events[1]["actor"] == "anonymous" and events[1]["duration_ms"] is not None
    # Directory filter: whole path segments, as the search path_filter.
    under = client.get("/api/indexing/events", params={"directory": "/data/codebase/acme"}).json()["items"]
    assert [e["file_path"] for e in under] == ["/data/codebase/acme/api/main.go"]
    assert client.get("/api/indexing/events", params={"directory": "/data/codebase/ac"}).json()["items"] == []


def test_summary_counts_outcomes_and_dates_each_channel(client):
    for path in ("a.go", "b.go"):
        client.post("/api/index", headers={"X-Mnemos-Client": "connector"},
                    json={"file_path": path, "collection": "mnemos_code", "content": "x"})
    client.app.state.indexer.index_file.side_effect = RuntimeError("Qdrant timeout")
    with pytest.raises(RuntimeError):
        client.post("/api/index", json={"file_path": "c.go", "collection": "mnemos_code", "content": "x"})

    job = ReindexJob(client.app.state.index_events, collection="mnemos_code", path="/data/codebase",
                     full=True, actor="root", channel="cli")
    job.start(40)
    for i in range(40):
        job.file_done(f"f{i}.py", 1)
    job.finish()

    summary = client.get("/api/indexing/summary").json()
    assert summary["counts"] == {"indexed": 2, "deleted": 0, "skipped": 0, "failed": 1, "refused": 0}
    assert summary["job_files"] == 40
    # Every channel is listed, active or not: a silent watcher must show as silent.
    by_channel = {c["channel"]: c for c in summary["channels"]}
    assert list(by_channel) == ["watcher", "hook", "connector", "cli", "dashboard", "mcp", "api", "script"]
    assert by_channel["watcher"]["last_at"] is None
    assert by_channel["cli"]["last_file"] == "/data/codebase" and by_channel["connector"]["last_at"]
    assert summary["retention_days"] == 30


def test_a_refused_push_or_delete_is_an_event(client, monkeypatch):
    from server.principal import forbidden

    def refuse(*_args, **_kwargs):
        raise forbidden("Tenant 'dev' cannot write tags ['acme']")

    monkeypatch.setattr("server.api._require_push", refuse)
    monkeypatch.setattr("server.api._require_delete", refuse)
    assert client.post("/api/index", json={"file_path": "a.go", "collection": "mnemos_code", "content": "x"}).status_code == 403
    assert client.delete("/api/index/mnemos_code/b.go").status_code == 403

    events = client.get("/api/indexing/events", params={"op": "refused"}).json()["items"]
    assert [e["file_path"] for e in events] == ["b.go", "a.go"]
    assert "cannot write" in events[0]["error"]


def test_a_running_job_that_stopped_moving_is_flagged(client):
    log = client.app.state.index_events
    alive = ReindexJob(log, collection="mnemos_code", path="/data/codebase", full=True, actor="root", channel="cli")
    lost = ReindexJob(log, collection="mnemos_docs", path="/data/docs", full=True, actor="root", channel="cli")
    log.update(lost.id, {"updated_ts": time.time() - 600})

    jobs = {j["id"]: j for j in client.get("/api/indexing/jobs").json()["items"]}
    assert jobs[alive.id]["stalled"] is False and jobs[lost.id]["stalled"] is True
