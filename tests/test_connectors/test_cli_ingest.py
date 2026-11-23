"""`mnemos ingest …` wired end to end on mock transports."""

from __future__ import annotations

import httpx
import pytest
from click.testing import CliRunner

import connectors.transport
from cli.main import cli

from .conftest import MNEMOS_URL, FakeMnemos, Router

JIRA = "https://jira.example.com"


class ConnectorsApi:
    """`GET /api/connectors` of the server, as the dashboard leaves it."""

    def __init__(self) -> None:
        self.revision = 1
        self.status = 200
        self.interval: str | None = None
        self.sources: list[dict] = [_server_jira("demo")]
        self.requests: list[httpx.Request] = []

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self.status != 200:
            return httpx.Response(self.status, json={"detail": "Forbidden"})
        return httpx.Response(200, json={
            "revision": self.revision, "writable": True, "interval": self.interval,
            "sources": self.sources, "problems": [],
        })


def _server_jira(*projects: str) -> dict:
    return {
        "type": "jira", "name": "tracker", "url": JIRA, "token_env": "TRACKER_TOKEN",
        "projects": list(projects), "tags": ["team-a"],
    }


@pytest.fixture
def connectors_api() -> ConnectorsApi:
    return ConnectorsApi()


@pytest.fixture
def wired(monkeypatch, mnemos: FakeMnemos, router: Router, connectors_api: ConnectorsApi):
    """Route every client the CLI builds: mnemos.test → FakeMnemos (and the
    connectors API), the rest → router."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "mnemos.test":
            if request.url.path == "/api/connectors":
                return connectors_api.handle(request)
            return mnemos.handle(request)
        return router(request)

    def make_client(**kwargs) -> httpx.Client:
        kwargs.pop("timeout", None)
        headers = {"User-Agent": "test", **(kwargs.pop("headers", None) or {})}
        return httpx.Client(transport=httpx.MockTransport(handler), headers=headers, follow_redirects=True, **kwargs)

    monkeypatch.setattr(connectors.transport, "make_client", make_client)
    monkeypatch.setenv("MNEMOS_URL", MNEMOS_URL)
    monkeypatch.setenv("MNEMOS_API_KEY", "mnemos-key")
    for var in (
        "JIRA_URL", "JIRA_USER", "JIRA_TOKEN", "CONFLUENCE_URL", "CONFLUENCE_USER", "CONFLUENCE_TOKEN",
        "MNEMOS_CONNECTORS_CONFIG",
    ):
        monkeypatch.delenv(var, raising=False)
    return mnemos, router


def _issue(key: str) -> dict:
    return {
        "id": key.split("-")[1],
        "key": key,
        "fields": {
            "summary": f"Summary {key}",
            "issuetype": {"name": "Task"},
            "status": {"name": "Open"},
            "updated": "2024-03-02T10:00:00.000+0100",
            "description": "h2. Context\nSome *context*.",
            "comment": {"comments": [], "total": 0},
        },
    }


def test_ingest_jira_end_to_end(wired, monkeypatch):
    mnemos, router = wired
    router.add(f"{JIRA}/rest/api/2/search", {"startAt": 0, "maxResults": 100, "total": 2, "issues": [_issue("DEMO-1"), _issue("DEMO-2")]})
    mnemos.seed("jira/DEMO/DEMO-9.md", 1.0, ["jira", "jira:DEMO"])
    monkeypatch.setenv("JIRA_URL", JIRA)
    monkeypatch.setenv("JIRA_TOKEN", "jira-pat")

    result = CliRunner().invoke(cli, ["ingest", "jira", "--project", "demo", "--tags", "team-a, ops"])

    assert result.exit_code == 0, result.output
    assert "jira:DEMO" in result.output
    # Mnemos calls carry MNEMOS_API_KEY, Jira calls the Jira PAT.
    assert mnemos.requests and all(r.headers["Authorization"] == "Bearer mnemos-key" for r in mnemos.requests)
    assert router.requests and all(r.headers["Authorization"] == "Bearer jira-pat" for r in router.requests)
    assert sorted(mnemos.pushed_paths) == ["jira/DEMO/DEMO-1.md", "jira/DEMO/DEMO-2.md"]
    assert mnemos.pushed[0]["tags"] == ["team-a", "ops", "jira", "jira:DEMO"]
    assert mnemos.deleted == ["jira/DEMO/DEMO-9.md"]


def test_ingest_exits_non_zero_when_a_push_fails(wired, monkeypatch):
    mnemos, router = wired
    router.add(f"{JIRA}/rest/api/2/search", {"startAt": 0, "maxResults": 100, "total": 2, "issues": [_issue("DEMO-1"), _issue("DEMO-2")]})
    mnemos.fail_push_for.add("jira/DEMO/DEMO-1.md")

    result = CliRunner().invoke(cli, ["ingest", "jira", "--url", JIRA, "--token", "jira-pat", "--project", "DEMO"])

    assert result.exit_code == 1
    assert mnemos.pushed_paths == ["jira/DEMO/DEMO-2.md"]


def test_ingest_web_rejects_non_http_urls(wired):
    result = CliRunner().invoke(cli, ["ingest", "web", "ftp://files.example.com/"])
    assert result.exit_code == 2
    assert "Not an http(s) URL" in result.output


def test_ingest_requires_a_token(wired):
    result = CliRunner().invoke(cli, ["ingest", "confluence", "--url", "https://wiki.example.com", "--space", "ENG"])
    assert result.exit_code == 2
    assert "--token" in result.output


# ---------------------------------------------------------------------------
# config file: `ingest run` / `ingest serve`
# ---------------------------------------------------------------------------


def _write_config(tmp_path, projects: str = "[demo]") -> str:
    path = tmp_path / "connectors.yaml"
    path.write_text(
        "sources:\n"
        "  - type: jira\n"
        "    name: tracker\n"
        f"    url: {JIRA}\n"
        "    token_env: TRACKER_TOKEN\n"
        f"    projects: {projects}\n"
        "    tags: [team-a]\n"
        "  - type: web\n"
        "    name: docs\n"
        "    urls: [https://docs.example.com/]\n"
    )
    return str(path)


def test_ingest_run_syncs_every_source_of_the_config(wired, monkeypatch, tmp_path):
    mnemos, router = wired
    router.add(f"{JIRA}/rest/api/2/search", {"startAt": 0, "maxResults": 100, "total": 1, "issues": [_issue("DEMO-1")]})
    router.add("https://docs.example.com/robots.txt", httpx.Response(404))
    router.add("https://docs.example.com/", httpx.Response(200, html="<title>Docs</title><main>Hi</main>"))
    monkeypatch.setenv("TRACKER_TOKEN", "jira-pat")

    result = CliRunner().invoke(cli, ["ingest", "run", "--config", _write_config(tmp_path)])

    assert result.exit_code == 0, result.output
    assert "jira:DEMO" in result.output and "web:docs.example.com" in result.output
    assert sorted(mnemos.pushed_paths)[0] == "jira/DEMO/DEMO-1.md"
    assert len(mnemos.pushed_paths) == 2


def test_ingest_run_only_selects_sources_by_name(wired, monkeypatch, tmp_path):
    mnemos, router = wired
    router.add(f"{JIRA}/rest/api/2/search", {"startAt": 0, "maxResults": 100, "total": 1, "issues": [_issue("DEMO-1")]})
    monkeypatch.setenv("TRACKER_TOKEN", "jira-pat")

    result = CliRunner().invoke(cli, ["ingest", "run", "--config", _write_config(tmp_path), "--only", "tracker"])

    assert result.exit_code == 0, result.output
    assert mnemos.pushed_paths == ["jira/DEMO/DEMO-1.md"]
    assert not any(r.url.host == "docs.example.com" for r in router.requests)


def test_ingest_run_rejects_an_unknown_only_name(wired, monkeypatch, tmp_path):
    monkeypatch.setenv("TRACKER_TOKEN", "jira-pat")
    result = CliRunner().invoke(cli, ["ingest", "run", "--config", _write_config(tmp_path), "--only", "wiki"])
    assert result.exit_code == 2
    assert "wiki" in result.output


def test_ingest_run_reports_config_errors_without_syncing(wired, tmp_path):
    mnemos, _ = wired
    result = CliRunner().invoke(cli, ["ingest", "run", "--config", _write_config(tmp_path)])
    assert result.exit_code == 1
    assert "TRACKER_TOKEN is not set" in result.output
    assert mnemos.requests == []


def test_ingest_run_reads_the_config_path_from_the_environment(wired, monkeypatch, tmp_path):
    mnemos, router = wired
    router.add(f"{JIRA}/rest/api/2/search", {"startAt": 0, "maxResults": 100, "total": 0, "issues": []})
    monkeypatch.setenv("TRACKER_TOKEN", "jira-pat")
    monkeypatch.setenv("MNEMOS_CONNECTORS_CONFIG", _write_config(tmp_path))

    result = CliRunner().invoke(cli, ["ingest", "run", "--only", "tracker"])

    assert result.exit_code == 0, result.output
    assert "jira:DEMO" in result.output


def test_ingest_serve_syncs_at_start_and_rereads_the_config_on_each_run(wired, monkeypatch, tmp_path):
    import connectors.runner

    mnemos, router = wired
    router.add(f"{JIRA}/rest/api/2/search", lambda r: httpx.Response(200, json={
        "startAt": 0, "maxResults": 100, "total": 1,
        "issues": [_issue("OPS-1" if "OPS" in r.url.params["jql"] else "DEMO-1")],
    }))
    router.add("https://docs.example.com/robots.txt", httpx.Response(404))
    router.add("https://docs.example.com/", httpx.Response(200, html="<title>Docs</title><main>Hi</main>"))
    monkeypatch.setenv("TRACKER_TOKEN", "jira-pat")
    config_path = _write_config(tmp_path)

    def fake_serve(run_once, *, interval, trigger, stop):
        run_once()                                  # start-up sync
        _write_config(tmp_path, projects="[ops]")   # edited while the service runs
        run_once()                                  # what SIGHUP / the timer triggers

    monkeypatch.setattr(connectors.runner, "serve", fake_serve)

    result = CliRunner().invoke(cli, ["ingest", "serve", "--config", config_path])

    assert result.exit_code == 0, result.output
    assert "jira/DEMO/DEMO-1.md" in mnemos.pushed_paths
    assert "jira/OPS/OPS-1.md" in mnemos.pushed_paths


def test_ingest_serve_refuses_to_start_on_a_bad_config(wired, tmp_path):
    result = CliRunner().invoke(cli, ["ingest", "serve", "--config", str(tmp_path / "absent.yaml")])
    assert result.exit_code == 1
    assert "not found" in result.output


# ---------------------------------------------------------------------------
# server config: `ingest run` / `ingest serve` without --config
# ---------------------------------------------------------------------------


def _jira_search_by_project(router: Router) -> None:
    router.add(f"{JIRA}/rest/api/2/search", lambda r: httpx.Response(200, json={
        "startAt": 0, "maxResults": 100, "total": 1,
        "issues": [_issue("OPS-1" if "OPS" in r.url.params["jql"] else "DEMO-1")],
    }))


def test_ingest_run_reads_the_sources_from_the_server(wired, monkeypatch, connectors_api):
    mnemos, router = wired
    _jira_search_by_project(router)
    monkeypatch.setenv("TRACKER_TOKEN", "jira-pat")

    result = CliRunner().invoke(cli, ["ingest", "run"])

    assert result.exit_code == 0, result.output
    assert mnemos.pushed_paths == ["jira/DEMO/DEMO-1.md"]
    assert mnemos.pushed[0]["tags"] == ["team-a", "jira", "jira:DEMO"]
    assert [r.headers["Authorization"] for r in connectors_api.requests] == ["Bearer mnemos-key"]


def test_ingest_run_fails_clearly_when_the_server_config_is_unreadable(wired, monkeypatch, connectors_api):
    mnemos, _ = wired
    connectors_api.status = 403
    monkeypatch.setenv("TRACKER_TOKEN", "jira-pat")

    result = CliRunner().invoke(cli, ["ingest", "run"])

    assert result.exit_code == 1
    assert f"cannot read the connectors from {MNEMOS_URL}/api/connectors" in result.output
    assert "403" in result.output
    assert mnemos.requests == []


def test_ingest_run_rejects_an_invalid_server_config(wired, monkeypatch, connectors_api):
    mnemos, _ = wired
    monkeypatch.setenv("TRACKER_TOKEN", "jira-pat")
    connectors_api.sources = []

    result = CliRunner().invoke(cli, ["ingest", "run"])

    assert result.exit_code == 1
    assert "server:" in result.output and "no source declared" in result.output
    assert mnemos.requests == []


def test_ingest_serve_follows_the_server_config_and_keeps_it_through_failures(wired, monkeypatch, connectors_api):
    import connectors.runner

    mnemos, router = wired
    _jira_search_by_project(router)
    monkeypatch.setenv("TRACKER_TOKEN", "jira-pat")
    watched: list[tuple] = []

    def fake_watch_revision(read_revision, initial, trigger, stop, *, poll=5.0):
        watched.append((read_revision, initial))

    def fake_serve(run_once, *, interval, trigger, stop):
        run_once()                                   # start-up sync: DEMO
        connectors_api.revision, connectors_api.sources = 2, [_server_jira("ops")]
        run_once()                                   # after a dashboard save: OPS
        connectors_api.status = 403
        run_once()                                   # server refuses: still OPS
        connectors_api.status, connectors_api.sources = 200, []
        run_once()                                   # broken save: still OPS

    monkeypatch.setattr(connectors.runner, "watch_revision", fake_watch_revision)
    monkeypatch.setattr(connectors.runner, "serve", fake_serve)

    result = CliRunner().invoke(cli, ["ingest", "serve"])

    assert result.exit_code == 0, result.output
    assert f"{MNEMOS_URL}/api/connectors" in result.output
    assert result.output.count("keeping the previous configuration") == 2
    jqls = [r.url.params["jql"] for r in router.requests if r.url.path == "/rest/api/2/search"]
    assert len(jqls) == 4
    assert "DEMO" in jqls[0] and all("OPS" in jql for jql in jqls[1:])
    # The watcher starts from the revision loaded at start-up and reads the live one.
    read_revision, initial = watched[0]
    assert initial == 1
    connectors_api.revision = 7
    assert read_revision() == 7


def test_ingest_serve_refuses_to_start_without_a_server_config(wired, connectors_api):
    connectors_api.status = 403
    result = CliRunner().invoke(cli, ["ingest", "serve"])
    assert result.exit_code == 1
    assert "cannot read the connectors" in result.output
