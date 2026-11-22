from __future__ import annotations

import textwrap
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from connectors.config import load_config
from server.config import settings

JIRA = {
    "type": "jira", "name": "tracker", "url": "https://jira.example.com",
    "token_env": "TRACKER_TOKEN", "projects": ["OPS"],
}


@pytest.fixture
def path(tmp_path, monkeypatch):
    path = tmp_path / "config" / "connectors.yaml"
    path.parent.mkdir()
    monkeypatch.setattr(settings, "mnemos_connectors_config", str(path))
    return path


@pytest.fixture
def make_client(path, tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "mnemos_state_dir", str(tmp_path / "state"))

    def build(writable: bool = True) -> TestClient:
        monkeypatch.setattr(settings, "mnemos_ui_config_writable", writable)
        with patch("server.main.QdrantClient"), patch("server.main.EmbeddingService"):
            from server.main import create_app
            return TestClient(create_app())

    return build


def test_absent_file_is_an_empty_config(make_client):
    body = make_client().get("/api/connectors").json()
    assert body["exists"] is False
    assert body["sources"] == [] and body["problems"] == []


def test_saved_file_is_what_the_ingester_loads(make_client, path):
    resp = make_client().put("/api/connectors", json={
        "interval": " 30m ",
        "sources": [
            # Empty optional values are dropped, not written as `user: ''`.
            {**JIRA, "user": "", "jql": None, "tags": []},
            {"type": "web", "urls": ["https://docs.example.com/"], "max_pages": 20},
        ],
    })
    assert resp.status_code == 200, resp.text
    assert resp.json()["problems"] == []

    config = load_config(path, environ={"TRACKER_TOKEN": "pat"})
    assert config.interval == 1800
    tracker, docs = config.sources
    assert (tracker.user, tracker.jql, tracker.tags, tracker.token) == (None, None, (), "pat")
    assert docs.max_pages == 20
    assert "user:" not in path.read_text()


def test_invalid_put_saves_nothing_and_lists_every_problem(make_client, path):
    path.write_text("sources: []\n")
    resp = make_client().put("/api/connectors", json={
        "interval": "soon",
        "sources": [{**JIRA, "projects": []}, {"type": "web", "urls": ["ftp://x"], "token": "s"}],
    })
    assert resp.status_code == 422
    problems = resp.json()["problems"]
    assert any("invalid interval" in p for p in problems)
    assert "sources[0] (tracker): missing 'projects'" in problems
    assert any("unknown key 'token'" in p for p in problems)
    assert path.read_text() == "sources: []\n"


def test_put_is_forbidden_when_not_writable(make_client, path):
    resp = make_client(writable=False).put("/api/connectors", json={"sources": [JIRA]})
    assert resp.status_code == 403
    assert not path.exists()


def test_get_reports_problems_without_echoing_unknown_keys(make_client, path):
    # A token pasted into the file by mistake must not reach the browser.
    path.write_text(textwrap.dedent("""
        sources:
          - type: confluence
            url: https://wiki.example.com
            token_env: WIKI_TOKEN
            token: secret-pat-4242
            spaces: [ENG]
    """))
    resp = make_client().get("/api/connectors")
    assert "secret-pat-4242" not in resp.text
    body = resp.json()
    assert body["sources"] == [{
        "type": "confluence", "url": "https://wiki.example.com",
        "token_env": "WIKI_TOKEN", "spaces": ["ENG"],
    }]
    # Validated without the ingester's environment: an unset WIKI_TOKEN is not a problem here.
    assert body["problems"] == ["sources[0] (confluence): unknown key 'token'"]


def test_saving_through_a_symlink_keeps_the_link(make_client, path, tmp_path):
    target = tmp_path / "dotfiles.yaml"
    target.write_text("sources: []\n")
    path.symlink_to(target)

    assert make_client().put("/api/connectors", json={"sources": [JIRA]}).status_code == 200
    assert path.is_symlink()
    assert "TRACKER_TOKEN" in target.read_text()
