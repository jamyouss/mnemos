"""Shared fakes for the connector tests: an in-memory Mnemos server and a
router turning ``httpx.MockTransport`` into a tiny multi-host web."""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass, field
from urllib.parse import unquote

import httpx
import pytest

from connectors.mnemos import MnemosClient
from connectors.transport import RetryPolicy

MNEMOS_URL = "http://mnemos.test"
NO_WAIT = RetryPolicy(attempts=3, sleep=lambda _s: None)


@dataclass
class FakeMnemos:
    """The three knowledge endpoints of the server, backed by a dict."""

    files: dict[str, dict] = field(default_factory=dict)
    pushed: list[dict] = field(default_factory=list)
    deleted: list[str] = field(default_factory=list)
    requests: list[httpx.Request] = field(default_factory=list)
    fail_push_for: set[str] = field(default_factory=set)

    def seed(self, file_path: str, mtime: float | None, tags: list[str]) -> None:
        self.files[file_path] = {"file_mtime": mtime, "tags": tags}

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        path = request.url.path
        prefix = "/api/index/mnemos_knowledge/"
        if request.method == "GET" and path == prefix + "files":
            tag = request.url.params["tag"]
            files = {p: f["file_mtime"] for p, f in self.files.items() if tag in f["tags"]}
            return httpx.Response(200, json={"collection": "mnemos_knowledge", "tag": tag, "files": files})
        if request.method == "POST" and path == "/api/index":
            body = json.loads(request.content)
            if body["file_path"] in self.fail_push_for:
                return httpx.Response(400, json={"detail": "rejected"})
            self.pushed.append(body)
            self.files[body["file_path"]] = {"file_mtime": body.get("file_mtime"), "tags": body["tags"]}
            return httpx.Response(200, json={"status": "indexed", "chunks": 1})
        if request.method == "DELETE" and path.startswith(prefix):
            file_path = unquote(request.url.raw_path.decode()[len(prefix):])
            self.deleted.append(file_path)
            self.files.pop(file_path, None)
            return httpx.Response(200, json={"status": "deleted"})
        return httpx.Response(404, json={"detail": f"no route {request.method} {path}"})

    def client(self) -> MnemosClient:
        return MnemosClient(
            MNEMOS_URL, httpx.Client(transport=httpx.MockTransport(self.handle)), retry=NO_WAIT
        )

    @property
    def pushed_paths(self) -> list[str]:
        return [p["file_path"] for p in self.pushed]


Handler = Callable[[httpx.Request], httpx.Response]


class Router:
    """Dispatch requests by ``(host, path)``; records every request seen."""

    def __init__(self) -> None:
        self.routes: dict[tuple[str, str], Handler] = {}
        self.requests: list[httpx.Request] = []

    def add(self, url: str, handler: Handler | httpx.Response | dict) -> None:
        parsed = httpx.URL(url)
        if isinstance(handler, dict):
            payload = handler
            handler = lambda _r, p=payload: httpx.Response(200, json=p)  # noqa: E731
        elif isinstance(handler, httpx.Response):
            response = handler
            handler = lambda _r, resp=response: resp  # noqa: E731
        self.routes[(parsed.host, parsed.path)] = handler

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        handler = self.routes.get((request.url.host, request.url.path))
        if handler is None:
            return httpx.Response(404, text="not found")
        return handler(request)

    def client(self, **kwargs) -> httpx.Client:
        return httpx.Client(transport=httpx.MockTransport(self), follow_redirects=True, **kwargs)

    def paths(self) -> list[str]:
        return [r.url.path for r in self.requests]


@pytest.fixture
def mnemos() -> FakeMnemos:
    return FakeMnemos()


@pytest.fixture
def router() -> Router:
    return Router()
