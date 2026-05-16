from __future__ import annotations

from unittest.mock import MagicMock, patch

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
                "mnemos_code_myproject": {"vectors_count": 42, "points_count": 42, "status": "green"},
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


# ---------------------------------------------------------------------------
# rag search-skills
# ---------------------------------------------------------------------------


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
        {"status": "reindexed", "collection": "mnemos_code_myproject", "chunks_indexed": 150}
    )
    with patch("httpx.post", return_value=mock_resp):
        result = runner.invoke(cli, ["reindex", "--collection", "mnemos_code_myproject"])
    assert result.exit_code == 0
    assert "reindex" in result.output.lower() or "mnemos_code_myproject" in result.output


def test_reindex_with_path():
    runner = CliRunner()
    mock_resp = _mock_response(
        {"status": "reindexed", "collection": "mnemos_code_myproject", "chunks_indexed": 10}
    )
    with patch("httpx.post", return_value=mock_resp) as mock_post:
        result = runner.invoke(
            cli, ["reindex", "--collection", "mnemos_code_myproject", "--path", "/data/src", "--full"]
        )
    assert result.exit_code == 0
    posted_json = mock_post.call_args[1].get("json") or mock_post.call_args[0][1]
    assert posted_json.get("full") is True
    assert posted_json.get("path") == "/data/src"


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
