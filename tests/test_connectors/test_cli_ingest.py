"""`mnemos ingest …` wired end to end on mock transports."""

from __future__ import annotations

import httpx
import pytest
from click.testing import CliRunner

import connectors.transport
from cli.main import cli

from .conftest import MNEMOS_URL, FakeMnemos, Router

JIRA = "https://jira.example.com"


@pytest.fixture
def wired(monkeypatch, mnemos: FakeMnemos, router: Router):
    """Route every client the CLI builds: mnemos.test → FakeMnemos, the rest → router."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "mnemos.test":
            return mnemos.handle(request)
        return router(request)

    def make_client(**kwargs) -> httpx.Client:
        kwargs.pop("timeout", None)
        headers = {"User-Agent": "test", **(kwargs.pop("headers", None) or {})}
        return httpx.Client(transport=httpx.MockTransport(handler), headers=headers, follow_redirects=True, **kwargs)

    monkeypatch.setattr(connectors.transport, "make_client", make_client)
    monkeypatch.setenv("MNEMOS_URL", MNEMOS_URL)
    monkeypatch.setenv("MNEMOS_API_KEY", "mnemos-key")
    for var in ("JIRA_URL", "JIRA_USER", "JIRA_TOKEN", "CONFLUENCE_URL", "CONFLUENCE_USER", "CONFLUENCE_TOKEN"):
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
