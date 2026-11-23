from __future__ import annotations

import threading

import httpx
import pytest

import connectors.transport
from connectors.config import ConnectorsConfig, JiraConfig, WebConfig
from connectors.runner import run_sources, serve, watch_file, watch_revision

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


def test_serve_syncs_at_start_then_on_each_trigger_until_stopped():
    trigger, stop = threading.Event(), threading.Event()
    runs: list[int] = []

    def run_once() -> None:
        runs.append(len(runs))
        if len(runs) < 3:
            trigger.set()  # what SIGHUP does: resync now
        else:
            stop.set()

    serve(run_once, interval=None, trigger=trigger, stop=stop)

    assert runs == [0, 1, 2]


def test_serve_resyncs_after_the_interval():
    trigger, stop = threading.Event(), threading.Event()
    runs: list[int] = []

    def run_once() -> None:
        runs.append(1)
        if len(runs) == 2:
            stop.set()

    serve(run_once, interval=0.01, trigger=trigger, stop=stop)

    assert len(runs) == 2


def test_serve_keeps_running_when_a_run_raises():
    trigger, stop = threading.Event(), threading.Event()
    runs: list[int] = []

    def run_once() -> None:
        runs.append(1)
        if len(runs) == 1:
            raise RuntimeError("mnemos unreachable")
        stop.set()

    serve(run_once, interval=0.01, trigger=trigger, stop=stop)

    assert len(runs) == 2


def test_watch_file_triggers_on_an_atomic_replace(tmp_path):
    import os

    path = tmp_path / "connectors.yaml"
    path.write_text("interval: 6h\n")
    trigger, stop = threading.Event(), threading.Event()
    thread = threading.Thread(target=watch_file, args=(str(path), trigger, stop), kwargs={"poll": 0.01})
    thread.start()
    try:
        assert not trigger.wait(0.05)  # nothing changed yet
        # What the dashboard does: same size, possibly same mtime, new inode.
        replacement = tmp_path / "next.yaml"
        replacement.write_text("interval: 1h\n")
        os.replace(replacement, path)
        assert trigger.wait(2)
    finally:
        stop.set()
        thread.join()


def test_watch_revision_triggers_on_a_new_revision_only():
    trigger, stop = threading.Event(), threading.Event()
    # Same revision, a failed read, same again, then a dashboard save.
    answers = iter([3, RuntimeError("server down"), 3, 4])
    triggered_before_the_save: list[bool] = []

    def read_revision() -> int:
        answer = next(answers, 4)
        if answer == 4 and not triggered_before_the_save:
            triggered_before_the_save.append(trigger.is_set())
        if isinstance(answer, Exception):
            raise answer
        return answer

    thread = threading.Thread(
        target=watch_revision, args=(read_revision, 3, trigger, stop), kwargs={"poll": 0.01}
    )
    thread.start()
    try:
        assert trigger.wait(2)
        assert triggered_before_the_save == [False]
        trigger.clear()
        assert not trigger.wait(0.1)  # revision 4 stays put: no second resync
    finally:
        stop.set()
        thread.join()


def test_watch_revision_survives_a_server_outage():
    trigger, stop = threading.Event(), threading.Event()
    reads: list[int] = []

    def read_revision() -> int:
        reads.append(1)
        raise RuntimeError("connection refused")

    thread = threading.Thread(
        target=watch_revision, args=(read_revision, 1, trigger, stop), kwargs={"poll": 0.01}
    )
    thread.start()
    try:
        assert not trigger.wait(0.1)
    finally:
        stop.set()
        thread.join()
    assert len(reads) > 1  # kept polling after the first failure
