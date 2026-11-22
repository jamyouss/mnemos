from __future__ import annotations

import httpx

from eval.runner import EvalRunner
from eval.schema import GoldenItem


def test_runner_sends_the_api_key(monkeypatch):
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"results": []})

    real_client = httpx.Client
    monkeypatch.setattr(
        httpx, "Client", lambda **kw: real_client(transport=httpx.MockTransport(handler), **kw)
    )
    runner = EvalRunner(mnemos_url="http://mnemos.test", api_key="secret-a")
    runner.run([GoldenItem(id="q1", query="where is auth", intent="code_search", expected_files=["a.go"])])

    assert seen[0].headers["authorization"] == "Bearer secret-a"
