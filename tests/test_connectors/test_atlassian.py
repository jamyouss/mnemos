from __future__ import annotations

import base64

import httpx
import pytest

from connectors.atlassian import auth_kwargs, detect_deployment, site_root


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://example.atlassian.net", "cloud"),
        ("https://example.atlassian.net/wiki/", "cloud"),
        ("https://wiki.example.com/confluence", "datacenter"),
        ("https://jira.example.com", "datacenter"),
    ],
)
def test_detect_deployment_from_host(url, expected):
    assert detect_deployment(url) == expected


def test_explicit_deployment_wins():
    assert detect_deployment("https://jira.example.com", "cloud") == "cloud"
    assert detect_deployment("https://example.atlassian.net", "datacenter") == "datacenter"
    with pytest.raises(ValueError):
        detect_deployment("https://jira.example.com", "server")


def _authorization(**kwargs) -> str:
    seen: list[httpx.Request] = []
    transport = httpx.MockTransport(lambda r: seen.append(r) or httpx.Response(200))
    with httpx.Client(transport=transport, **kwargs) as client:
        client.get("https://api.example.com/")
    return seen[0].headers["Authorization"]


def test_user_and_token_use_basic_auth():
    header = _authorization(**auth_kwargs("bot@example.com", "api-token"))
    assert header == "Basic " + base64.b64encode(b"bot@example.com:api-token").decode()


def test_token_alone_is_a_bearer_pat():
    assert _authorization(**auth_kwargs(None, "pat-123")) == "Bearer pat-123"
    assert _authorization(**auth_kwargs("", "pat-123")) == "Bearer pat-123"


def test_token_is_required():
    with pytest.raises(ValueError):
        auth_kwargs("bot@example.com", "")


def test_site_root():
    assert site_root(" https://example.atlassian.net/wiki/ ", strip_suffix="/wiki") == "https://example.atlassian.net"
    assert site_root("https://wiki.example.com/confluence/") == "https://wiki.example.com/confluence"
