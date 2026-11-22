from __future__ import annotations

import json

import httpx
import pytest

from connectors.mnemos import MnemosClient

from .conftest import MNEMOS_URL, NO_WAIT


def _client(handler, **kwargs) -> MnemosClient:
    http = httpx.Client(transport=httpx.MockTransport(handler), **kwargs)
    return MnemosClient(MNEMOS_URL + "/", http, retry=NO_WAIT)


def test_list_files_returns_path_to_mtime():
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"files": {"web/example.com/abc.md": 12.5, "web/example.com/d.md": None}})

    files = _client(handler).list_files("web:example.com")

    assert files == {"web/example.com/abc.md": 12.5, "web/example.com/d.md": None}
    assert str(seen[0].url) == f"{MNEMOS_URL}/api/index/mnemos_knowledge/files?tag=web%3Aexample.com"


def test_push_payload_and_bearer_header():
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"status": "indexed"})

    client = _client(handler, headers={"Authorization": "Bearer k"})
    client.push(file_path="jira/OPS/OPS-1.md", content="# x", tags=["jira", "jira:OPS"], source_url="https://u", file_mtime=3.0)
    client.push(file_path="jira/OPS/OPS-2.md", content="# y", tags=["jira"], source_url="https://v", file_mtime=None)

    first, second = (json.loads(r.content) for r in seen)
    assert first == {
        "file_path": "jira/OPS/OPS-1.md",
        "collection": "mnemos_knowledge",
        "content": "# x",
        "tags": ["jira", "jira:OPS"],
        "source_url": "https://u",
        "file_mtime": 3.0,
    }
    assert "file_mtime" not in second
    assert all(r.headers["Authorization"] == "Bearer k" for r in seen)


def test_delete_quotes_the_path():
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={})

    _client(handler).delete("confluence/~jdoe/1 2.md")
    assert seen[0].method == "DELETE"
    assert seen[0].url.raw_path == b"/api/index/mnemos_knowledge/confluence/~jdoe/1%202.md"


def test_http_errors_raise():
    client = _client(lambda r: httpx.Response(401, json={"detail": "bad key"}))
    with pytest.raises(httpx.HTTPStatusError):
        client.list_files("jira:OPS")
