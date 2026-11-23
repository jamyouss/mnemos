from __future__ import annotations

import threading

import httpx
import pytest

import connectors.transport
from connectors.config import ConnectorsConfig, JiraConfig, WebConfig
from connectors.runner import SyncQueue, run_sources, serve, watch_file, watch_server

from .conftest import FakeMnemos, Router

JIRA = "https://jira.example.com"


@pytest.fixture
def sources(monkeypatch, router: Router):
    """Every source client the runner builds goes to the router."""

    def make_client(**kwargs) -> httpx.Client:
        kwargs.pop("timeout", None)
        return httpx.Client(transport=httpx.MockTransport(router), follow_redirects=True, **kwargs)

    monkeypatch.setattr(connectors.transport, "make_client", make_client)
    return router


def _jira(name: str = "tracker", projects=("DEMO",)) -> JiraConfig:
    return JiraConfig(
        name=name, url=JIRA, token="jira-pat", user=None, deployment="auto",
        projects=tuple(projects), jql=None, tags=("team-a",),
    )


def _issue(key: str) -> dict:
    return {
        "id": key.split("-")[1],
        "key": key,
        "fields": {
            "summary": f"Summary {key}",
            "issuetype": {"name": "Task"},
            "status": {"name": "Open"},
            "updated": "2024-03-02T10:00:00.000+0100",
            "description": "Body",
            "comment": {"comments": [], "total": 0},
        },
    }


def _search(*keys: str) -> dict:
    return {"startAt": 0, "maxResults": 100, "total": len(keys), "issues": [_issue(k) for k in keys]}


def test_runs_every_scope_of_every_source(sources, mnemos: FakeMnemos):
    sources.add(f"{JIRA}/rest/api/2/search", _search("DEMO-1"))
    sources.add("https://docs.example.com/robots.txt", httpx.Response(404))
    sources.add(
        "https://docs.example.com/",
        httpx.Response(200, html="<html><title>Docs</title><main><p>Hello</p></main></html>"),
    )
    config = ConnectorsConfig(
        interval=None,
        sources=(_jira(), WebConfig(name="docs", urls=("https://docs.example.com/",), max_pages=10, tags=())),
    )

    results = run_sources(config, mnemos.client())

    assert [r.scope_tag for r in results] == ["jira:DEMO", "web:docs.example.com"]
    assert all(r.ok for r in results)
    assert mnemos.pushed[0]["tags"] == ["team-a", "jira", "jira:DEMO"]
    # The Jira PAT goes to Jira only.
    assert sources.requests[0].headers["Authorization"] == "Bearer jira-pat"


def test_only_restricts_the_run_to_named_sources(sources, mnemos: FakeMnemos):
    sources.add(f"{JIRA}/rest/api/2/search", _search("DEMO-1"))
    config = ConnectorsConfig(
        interval=None,
        sources=(_jira(), WebConfig(name="docs", urls=("https://docs.example.com/",), max_pages=10, tags=())),
    )

    results = run_sources(config, mnemos.client(), only={"tracker"})

    assert [r.scope_tag for r in results] == ["jira:DEMO"]
    assert all("docs.example.com" not in str(r.url) for r in sources.requests)


def test_a_failing_source_does_not_stop_the_others(sources, mnemos: FakeMnemos):
    sources.add(f"{JIRA}/rest/api/2/search", _search("OK-1"))
    config = ConnectorsConfig(
        interval=None,
        sources=(
            # Invalid deployment: building this source raises before any request.
            JiraConfig(name="broken", url=JIRA, token="t", user=None, deployment="mainframe",
                       projects=("X",), jql=None, tags=()),
            _jira(projects=("OK",)),
        ),
    )

    results = run_sources(config, mnemos.client())

    assert [r.ok for r in results] == [False, True]
    assert "broken" in results[0].scope_tag
    assert mnemos.pushed_paths == ["jira/OK/OK-1.md"]


def test_full_forces_a_push_of_unchanged_documents(sources, mnemos: FakeMnemos):
    sources.add(f"{JIRA}/rest/api/2/search", _search("DEMO-1"))
    config = ConnectorsConfig(interval=None, sources=(_jira(),))
    run_sources(config, mnemos.client())
    mnemos.pushed.clear()

    assert run_sources(config, mnemos.client())[0].unchanged == 1
    assert mnemos.pushed == []
    assert run_sources(config, mnemos.client(), full=True)[0].pushed == 1


# ---------------------------------------------------------------------------
# serve loop
# ---------------------------------------------------------------------------


def test_serve_syncs_at_start_then_on_each_request_until_stopped():
    queue, stop = SyncQueue(), threading.Event()
    runs: list[set[str] | None] = []

    def run_once(only) -> None:
        runs.append(only)
        if len(runs) == 1:
            queue.request(["wiki"])  # what "Sync" on one source does
        elif len(runs) == 2:
            queue.request()  # what SIGHUP or "Sync all" does
        else:
            stop.set()

    serve(run_once, interval=None, queue=queue, stop=stop)

    assert runs == [None, {"wiki"}, None]


def test_requests_made_during_a_run_merge_into_the_next_one():
    queue = SyncQueue()
    queue.request(["wiki"])
    queue.request(["jira"])
    assert queue.take() == {"wiki", "jira"}
    queue.request(["wiki"])
    queue.request()
    assert queue.take() is None  # every source covers the named one
    queue.request([])
    assert not queue.ready.is_set()


def test_serve_resyncs_after_the_interval():
    queue, stop = SyncQueue(), threading.Event()
    runs: list[set[str] | None] = []

    def run_once(only) -> None:
        runs.append(only)
        if len(runs) == 2:
            stop.set()

    serve(run_once, interval=0.01, queue=queue, stop=stop)

    assert runs == [None, None]


def test_serve_keeps_running_when_a_run_raises():
    queue, stop = SyncQueue(), threading.Event()
    runs: list[int] = []

    def run_once(_only) -> None:
        runs.append(1)
        if len(runs) == 1:
            raise RuntimeError("mnemos unreachable")
        stop.set()

    serve(run_once, interval=0.01, queue=queue, stop=stop)

    assert len(runs) == 2


def test_watch_file_queues_a_sync_on_an_atomic_replace(tmp_path):
    import os

    path = tmp_path / "connectors.yaml"
    path.write_text("interval: 6h\n")
    queue, stop = SyncQueue(), threading.Event()
    thread = threading.Thread(target=watch_file, args=(str(path), queue, stop), kwargs={"poll": 0.01})
    thread.start()
    try:
        assert not queue.ready.wait(0.05)  # nothing changed yet
        # What the dashboard does: same size, possibly same mtime, new inode.
        replacement = tmp_path / "next.yaml"
        replacement.write_text("interval: 1h\n")
        os.replace(replacement, path)
        assert queue.ready.wait(2)
        assert queue.take() is None
    finally:
        stop.set()
        thread.join()


def _snapshot(revision: int, requests: list[dict], acked: int = 0) -> dict:
    return {"revision": revision, "sync": {"requests": requests, "acked_id": acked}}


def _watch(snapshots: list, revision: int, seen: int):
    """Run watch_server over `snapshots` (an exception entry is a failed read); return (queue, acks)."""
    queue, stop = SyncQueue(), threading.Event()
    acks: list[int] = []
    answers = iter(snapshots)

    def read():
        answer = next(answers, None)
        if answer is None:
            stop.set()
            raise RuntimeError("done")
        if isinstance(answer, Exception):
            raise answer
        return answer

    watch_server(read, revision, seen, queue, stop, ack=acks.append, poll=0.001)
    return queue, acks


def test_watch_server_queues_a_dashboard_save():
    queue, _ = _watch([_snapshot(3, []), RuntimeError("server down"), _snapshot(4, [])], revision=3, seen=0)
    assert queue.take() is None


def test_watch_server_ignores_an_unchanged_revision_and_failed_reads():
    queue, acks = _watch([_snapshot(3, []), RuntimeError("server down"), _snapshot(3, [])], revision=3, seen=0)
    assert not queue.ready.is_set()
    assert acks == []


def test_watch_server_queues_new_sync_requests_and_acknowledges_them():
    old = {"id": 1, "sources": None}
    snapshots = [
        _snapshot(3, [old]),
        _snapshot(3, [old, {"id": 2, "sources": ["wiki"]}]),
        _snapshot(3, [old, {"id": 2, "sources": ["wiki"]}, {"id": 3, "sources": ["jira"]}]),
    ]
    queue, acks = _watch(snapshots, revision=3, seen=1)

    # Request 1 predates start-up: covered by the start-up sync, acknowledged, not queued again.
    assert queue.take() == {"wiki", "jira"}
    assert acks == [1, 2, 3]


def test_watch_server_retries_a_failed_acknowledgement():
    queue, stop = SyncQueue(), threading.Event()
    calls: list[int] = []
    reads = iter([_snapshot(1, [{"id": 5, "sources": None}])] * 2)

    def ack(request_id: int) -> None:
        calls.append(request_id)
        if len(calls) == 1:
            raise RuntimeError("server down")

    def read():
        answer = next(reads, None)
        if answer is None:
            stop.set()
            raise RuntimeError("done")
        return answer

    watch_server(read, 1, 0, queue, stop, ack=ack, poll=0.001)
    assert calls == [5, 5]
    assert queue.take() is None
