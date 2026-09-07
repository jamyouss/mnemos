from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import httpx
import pytest
from click.testing import CliRunner

from cli.main import cli


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _mock_response(json_data: dict, status_code: int = 200) -> MagicMock:
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = json_data
    resp.raise_for_status = MagicMock()
    return resp


# ---------------------------------------------------------------------------
# rag status
# ---------------------------------------------------------------------------


def test_status_healthy():
    runner = CliRunner()
    mock_resp = _mock_response(
        {
            "status": "healthy",
            "collections": {
                "mnemos_code": {"vectors_count": 42, "points_count": 42, "status": "green"},
            },
        }
    )
    with patch("httpx.get", return_value=mock_resp) as mock_get:
        result = runner.invoke(cli, ["status"])
    assert result.exit_code == 0
    assert "healthy" in result.output
    mock_get.assert_called_once()


def test_status_connection_error():
    runner = CliRunner()
    import httpx

    with patch("httpx.get", side_effect=httpx.ConnectError("refused")):
        result = runner.invoke(cli, ["status"])
    assert result.exit_code != 0 or "error" in result.output.lower() or "Error" in result.output


# ---------------------------------------------------------------------------
# rag search
# ---------------------------------------------------------------------------


def test_search_returns_results():
    runner = CliRunner()
    mock_resp = _mock_response(
        {
            "results": [
                {"file_path": "/src/main.go", "content": "func main() {}", "score": 0.9},
            ]
        }
    )
    with patch("httpx.post", return_value=mock_resp):
        result = runner.invoke(cli, ["search", "main function"])
    assert result.exit_code == 0
    assert "main.go" in result.output or "main function" in result.output or "results" in result.output.lower()


def test_search_no_results():
    runner = CliRunner()
    mock_resp = _mock_response({"results": []})
    with patch("httpx.post", return_value=mock_resp):
        result = runner.invoke(cli, ["search", "nothing here"])
    assert result.exit_code == 0


def test_search_with_limit():
    runner = CliRunner()
    mock_resp = _mock_response({"results": []})
    with patch("httpx.post", return_value=mock_resp) as mock_post:
        result = runner.invoke(cli, ["search", "--limit", "10", "query"])
    assert result.exit_code == 0
    call_kwargs = mock_post.call_args
    assert call_kwargs is not None


def test_search_with_tags():
    runner = CliRunner()
    mock_resp = _mock_response({"results": []})
    with patch("httpx.post", return_value=mock_resp) as mock_post:
        result = runner.invoke(cli, ["search", "--tags", "acme,webshop", "query"])
    assert result.exit_code == 0
    body = mock_post.call_args.kwargs["json"]
    assert body["tags_any"] == ["acme", "webshop"]
    assert "tags_all" not in body
    assert "project" not in body


def test_search_with_tags_all():
    runner = CliRunner()
    mock_resp = _mock_response({"results": []})
    with patch("httpx.post", return_value=mock_resp) as mock_post:
        result = runner.invoke(cli, ["search", "--tags-all", "acme,vue3", "query"])
    assert result.exit_code == 0
    body = mock_post.call_args.kwargs["json"]
    assert body["tags_all"] == ["acme", "vue3"]
    assert "tags_any" not in body


# ---------------------------------------------------------------------------
# rag search-code
# ---------------------------------------------------------------------------


def test_search_code_returns_results():
    runner = CliRunner()
    mock_resp = _mock_response(
        {
            "results": [
                {
                    "file_path": "/src/auth.go",
                    "content": "func Authenticate() {}",
                    "score": 0.85,
                    "language": "go",
                },
            ]
        }
    )
    with patch("httpx.post", return_value=mock_resp):
        result = runner.invoke(cli, ["search-code", "authentication handler"])
    assert result.exit_code == 0


def test_search_code_with_language():
    runner = CliRunner()
    mock_resp = _mock_response({"results": []})
    with patch("httpx.post", return_value=mock_resp) as mock_post:
        result = runner.invoke(cli, ["search-code", "--language", "go", "handler"])
    assert result.exit_code == 0
    posted_json = mock_post.call_args[1].get("json") or mock_post.call_args[0][1]
    assert posted_json.get("language") == "go"


def test_search_code_with_tags():
    runner = CliRunner()
    mock_resp = _mock_response({"results": []})
    with patch("httpx.post", return_value=mock_resp) as mock_post:
        result = runner.invoke(
            cli, ["search-code", "--tags", "acme,webshop-services", "handler"]
        )
    assert result.exit_code == 0
    posted_json = mock_post.call_args[1].get("json") or mock_post.call_args[0][1]
    assert posted_json.get("tags_any") == ["acme", "webshop-services"]
    assert "tags_all" not in posted_json
    assert "project" not in posted_json


def test_search_code_with_tags_all():
    runner = CliRunner()
    mock_resp = _mock_response({"results": []})
    with patch("httpx.post", return_value=mock_resp) as mock_post:
        result = runner.invoke(
            cli, ["search-code", "--tags-all", "acme,vue3", "handler"]
        )
    assert result.exit_code == 0
    posted_json = mock_post.call_args[1].get("json") or mock_post.call_args[0][1]
    assert posted_json.get("tags_all") == ["acme", "vue3"]
    assert "tags_any" not in posted_json
    assert "project" not in posted_json


def test_search_code_project_flag_removed():
    """--project must no longer be accepted by search-code."""
    runner = CliRunner()
    mock_resp = _mock_response({"results": []})
    with patch("httpx.post", return_value=mock_resp):
        result = runner.invoke(
            cli, ["search-code", "--project", "webshop", "handler"]
        )
    # Click exits with code 2 on unknown options (UsageError), not a crash.
    assert result.exit_code == 2
    assert "no such option" in result.output.lower() or "--project" in result.output


# ---------------------------------------------------------------------------
# rag search-skills
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# rag search-memory
# ---------------------------------------------------------------------------


def test_search_memory_with_tags():
    runner = CliRunner()
    mock_resp = _mock_response({"results": []})
    with patch("httpx.post", return_value=mock_resp) as mock_post:
        result = runner.invoke(
            cli, ["search-memory", "--tags", "decision", "auth"]
        )
    assert result.exit_code == 0
    called_url = mock_post.call_args[0][0]
    assert called_url.endswith("/api/search-memory")
    posted_json = mock_post.call_args[1].get("json") or mock_post.call_args[0][1]
    assert posted_json.get("tags_any") == ["decision"]
    assert "project" not in posted_json


def test_search_memory_with_tags_all():
    runner = CliRunner()
    mock_resp = _mock_response({"results": []})
    with patch("httpx.post", return_value=mock_resp) as mock_post:
        result = runner.invoke(
            cli, ["search-memory", "--tags-all", "webshop,decision", "auth"]
        )
    assert result.exit_code == 0
    posted_json = mock_post.call_args[1].get("json") or mock_post.call_args[0][1]
    assert posted_json.get("tags_all") == ["webshop", "decision"]
    assert "project" not in posted_json


def test_search_memory_project_flag_removed():
    """--project must no longer be accepted by search-memory."""
    runner = CliRunner()
    mock_resp = _mock_response({"results": []})
    with patch("httpx.post", return_value=mock_resp):
        result = runner.invoke(
            cli, ["search-memory", "--project", "webshop", "auth"]
        )
    assert result.exit_code == 2
    assert "no such option" in result.output.lower() or "--project" in result.output


def test_search_skills_returns_results():
    runner = CliRunner()
    mock_resp = _mock_response(
        {
            "results": [
                {
                    "skill_name": "code-reviewer",
                    "description": "Reviews code for quality and correctness",
                    "score": 0.8,
                },
            ]
        }
    )
    with patch("httpx.post", return_value=mock_resp):
        result = runner.invoke(cli, ["search-skills", "code review"])
    assert result.exit_code == 0
    assert "code-reviewer" in result.output or "Reviews code" in result.output


# ---------------------------------------------------------------------------
# rag reindex
# ---------------------------------------------------------------------------


def test_reindex_success():
    runner = CliRunner()
    mock_resp = _mock_response(
        {"status": "reindexed", "collection": "mnemos_code", "chunks_indexed": 150}
    )
    with patch("httpx.post", return_value=mock_resp):
        result = runner.invoke(cli, ["reindex", "--collection", "mnemos_code"])
    assert result.exit_code == 0
    assert "reindex" in result.output.lower() or "mnemos_code" in result.output


def test_reindex_with_path():
    runner = CliRunner()
    mock_resp = _mock_response(
        {"status": "reindexed", "collection": "mnemos_code", "chunks_indexed": 10}
    )
    with patch("httpx.post", return_value=mock_resp) as mock_post:
        result = runner.invoke(
            cli, ["reindex", "--collection", "mnemos_code", "--path", "/data/src", "--full"]
        )
    assert result.exit_code == 0
    posted_json = mock_post.call_args[1].get("json") or mock_post.call_args[0][1]
    assert posted_json.get("full") is True
    assert posted_json.get("path") == "/data/src"


def test_reindex_with_tags():
    runner = CliRunner()
    mock_resp = _mock_response(
        {"status": "reindex_started", "collection": "mnemos_code", "path": "/data/src"}
    )
    with patch("httpx.post", return_value=mock_resp) as mock_post:
        result = runner.invoke(
            cli,
            [
                "reindex",
                "--collection",
                "mnemos_code",
                "--path",
                "/data/src",
                "--tags",
                "webshop,globex,go",
            ],
        )
    assert result.exit_code == 0
    posted_json = mock_post.call_args[1].get("json") or mock_post.call_args[0][1]
    assert posted_json.get("tags") == ["webshop", "globex", "go"]
    assert "project" not in posted_json


def test_reindex_project_flag_removed():
    """--project must no longer be accepted by reindex."""
    runner = CliRunner()
    mock_resp = _mock_response({"status": "reindex_started"})
    with patch("httpx.post", return_value=mock_resp):
        result = runner.invoke(
            cli,
            [
                "reindex",
                "--collection",
                "mnemos_code",
                "--project",
                "webshop",
            ],
        )
    assert result.exit_code == 2
    assert "no such option" in result.output.lower() or "--project" in result.output


# ---------------------------------------------------------------------------
# rag memory list
# ---------------------------------------------------------------------------


def test_memory_list_empty():
    runner = CliRunner()
    mock_resp = _mock_response({"entries": []})
    with patch("httpx.get", return_value=mock_resp) as mock_get:
        result = runner.invoke(cli, ["memory", "list"])
    assert result.exit_code == 0
    # Default status filter must be "pending"
    params = mock_get.call_args[1].get("params") or {}
    assert params.get("status") == "pending"


def test_memory_list_with_entries():
    runner = CliRunner()
    mock_resp = _mock_response(
        {
            "entries": [
                {
                    "id": "abc-123",
                    "content": "Never use /admin in routes",
                    "status": "pending",
                    "memory_type": "convention",
                    "tags": ["routing"],
                    "created_at": "2024-01-01T00:00:00Z",
                }
            ]
        }
    )
    with patch("httpx.get", return_value=mock_resp):
        result = runner.invoke(cli, ["memory", "list"])
    assert result.exit_code == 0
    assert "abc-123" in result.output or "admin" in result.output or "pending" in result.output


def test_memory_list_with_status_filter():
    runner = CliRunner()
    mock_resp = _mock_response({"entries": []})
    with patch("httpx.get", return_value=mock_resp) as mock_get:
        result = runner.invoke(cli, ["memory", "list", "--status", "pending"])
    assert result.exit_code == 0
    call_args = mock_get.call_args
    # Check that status param was passed in params dict
    params = call_args[1].get("params") or {}
    assert params.get("status") == "pending"


# ---------------------------------------------------------------------------
# rag memory add
# ---------------------------------------------------------------------------


def test_memory_add_success():
    runner = CliRunner()
    mock_resp = _mock_response({"status": "created", "id": "new-id-456"})
    with patch("httpx.post", return_value=mock_resp) as mock_post:
        result = runner.invoke(cli, ["memory", "add", "Never use /admin in routes"])
    assert result.exit_code == 0
    assert "new-id-456" in result.output or "created" in result.output
    posted_json = mock_post.call_args[1].get("json") or mock_post.call_args[0][1]
    assert posted_json.get("status") == "approved"


def test_memory_add_with_options():
    runner = CliRunner()
    mock_resp = _mock_response({"status": "created", "id": "opt-id-789"})
    with patch("httpx.post", return_value=mock_resp) as mock_post:
        result = runner.invoke(
            cli,
            [
                "memory",
                "add",
                "--project",
                "myproject",
                "--type",
                "convention",
                "Use flat resource paths",
            ],
        )
    assert result.exit_code == 0
    posted_json = mock_post.call_args[1].get("json") or mock_post.call_args[0][1]
    assert posted_json.get("project") == "myproject"
    assert posted_json.get("memory_type") == "convention"


# ---------------------------------------------------------------------------
# rag memory approve / reject
# ---------------------------------------------------------------------------


def test_memory_approve():
    runner = CliRunner()
    mock_resp = _mock_response({"status": "approved", "id": "mem-id-001"})
    with patch("httpx.post", return_value=mock_resp) as mock_post:
        result = runner.invoke(cli, ["memory", "approve", "mem-id-001"])
    assert result.exit_code == 0
    assert "approved" in result.output or "mem-id-001" in result.output
    posted_json = mock_post.call_args[1].get("json") or mock_post.call_args[0][1]
    assert posted_json.get("action") == "approve"


def test_memory_reject():
    runner = CliRunner()
    mock_resp = _mock_response({"status": "rejected", "id": "mem-id-002"})
    with patch("httpx.post", return_value=mock_resp) as mock_post:
        result = runner.invoke(cli, ["memory", "reject", "mem-id-002"])
    assert result.exit_code == 0
    assert "rejected" in result.output or "mem-id-002" in result.output
    posted_json = mock_post.call_args[1].get("json") or mock_post.call_args[0][1]
    assert posted_json.get("action") == "reject"


# ---------------------------------------------------------------------------
# MNEMOS_URL env var
# ---------------------------------------------------------------------------


def test_custom_mnemos_url():
    runner = CliRunner()
    mock_resp = _mock_response({"status": "healthy", "collections": {}})
    with patch("httpx.get", return_value=mock_resp) as mock_get:
        result = runner.invoke(
            cli, ["status"], env={"MNEMOS_URL": "http://myserver:9999"}
        )
    assert result.exit_code == 0
    called_url = mock_get.call_args[0][0]
    assert "9999" in called_url or "myserver" in called_url


# ---------------------------------------------------------------------------
# Batch memory review
# ---------------------------------------------------------------------------


PENDING = {
    "entries": [
        {"id": "id-1", "content": "First decision.", "memory_type": "decision",
         "tags": ["a"], "status": "pending", "created_at": "2026-01-01"},
        {"id": "id-2", "content": "Second lesson.", "memory_type": "lesson",
         "tags": [], "status": "pending", "created_at": "2026-01-02"},
        {"id": "id-3", "content": "Third note.", "memory_type": "note",
         "tags": [], "status": "pending", "created_at": "2026-01-03"},
    ]
}


def _review_transport(reviewed: list):
    """httpx stub: GET returns the pending list, POST records the review."""
    def handler(request):
        if request.method == "GET":
            return httpx.Response(200, json=PENDING)
        reviewed.append((request.url.path, json.loads(request.content)["action"]))
        mem_id = request.url.path.split("/")[-2]
        action = json.loads(request.content)["action"]
        return httpx.Response(200, json={"id": mem_id, "status": action + "d"})
    return httpx.MockTransport(handler)


def test_review_walks_every_pending_entry(monkeypatch):
    """Reviewing used to mean copying a UUID out of a truncated table and
    running one command per entry, so nothing ever got approved."""
    reviewed: list = []
    transport = _review_transport(reviewed)
    monkeypatch.setattr(httpx, "get", lambda *a, **k: httpx.Client(transport=transport).get(*a, **k))
    monkeypatch.setattr(httpx, "post", lambda *a, **k: httpx.Client(transport=transport).post(*a, **k))

    result = CliRunner().invoke(cli, ["memory", "review"], input="a\nr\ns\n")

    assert result.exit_code == 0
    assert [a for _, a in reviewed] == ["approve", "reject"]
    assert "1 approved" in result.output
    assert "1 rejected" in result.output


def test_review_shows_the_full_content_not_a_truncation(monkeypatch):
    reviewed: list = []
    transport = _review_transport(reviewed)
    monkeypatch.setattr(httpx, "get", lambda *a, **k: httpx.Client(transport=transport).get(*a, **k))
    monkeypatch.setattr(httpx, "post", lambda *a, **k: httpx.Client(transport=transport).post(*a, **k))

    result = CliRunner().invoke(cli, ["memory", "review"], input="q\n")

    assert "First decision." in result.output
    assert "decision" in result.output


def test_review_quits_without_touching_the_rest(monkeypatch):
    reviewed: list = []
    transport = _review_transport(reviewed)
    monkeypatch.setattr(httpx, "get", lambda *a, **k: httpx.Client(transport=transport).get(*a, **k))
    monkeypatch.setattr(httpx, "post", lambda *a, **k: httpx.Client(transport=transport).post(*a, **k))

    CliRunner().invoke(cli, ["memory", "review"], input="a\nq\n")

    assert [a for _, a in reviewed] == ["approve"]


def test_review_warns_about_what_stays_pending(monkeypatch):
    """Only approved entries are searchable, so a half-done review has to say
    what is still invisible."""
    transport = _review_transport([])
    monkeypatch.setattr(httpx, "get", lambda *a, **k: httpx.Client(transport=transport).get(*a, **k))
    monkeypatch.setattr(httpx, "post", lambda *a, **k: httpx.Client(transport=transport).post(*a, **k))

    result = CliRunner().invoke(cli, ["memory", "review"], input="s\ns\ns\n")

    assert "3 still pending" in result.output


def test_approve_all_requires_confirmation(monkeypatch):
    reviewed: list = []
    transport = _review_transport(reviewed)
    monkeypatch.setattr(httpx, "get", lambda *a, **k: httpx.Client(transport=transport).get(*a, **k))
    monkeypatch.setattr(httpx, "post", lambda *a, **k: httpx.Client(transport=transport).post(*a, **k))

    declined = CliRunner().invoke(cli, ["memory", "review", "--approve-all"], input="n\n")
    assert reviewed == []
    assert "Aborted" in declined.output

    CliRunner().invoke(cli, ["memory", "review", "--approve-all"], input="y\n")
    assert [a for _, a in reviewed] == ["approve"] * 3


def test_review_with_nothing_pending(monkeypatch):
    def handler(request):
        return httpx.Response(200, json={"entries": []})
    transport = httpx.MockTransport(handler)
    monkeypatch.setattr(httpx, "get", lambda *a, **k: httpx.Client(transport=transport).get(*a, **k))

    result = CliRunner().invoke(cli, ["memory", "review"])
    assert "Nothing pending" in result.output
