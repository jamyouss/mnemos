from __future__ import annotations

from datetime import datetime, timedelta, timezone
from email.utils import format_datetime

import httpx
import pytest

from connectors.transport import (
    RetryPolicy,
    make_client,
    parse_http_date,
    parse_timestamp,
    request,
    retry_after_seconds,
)


def _sequence(*responses):
    """Handler answering each call with the next item (Response or exception)."""
    queue = list(responses)
    calls: list[httpx.Request] = []

    def handler(req: httpx.Request) -> httpx.Response:
        calls.append(req)
        item = queue.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    return httpx.Client(transport=httpx.MockTransport(handler)), calls


def _policy(waits: list[float], attempts: int = 4) -> RetryPolicy:
    return RetryPolicy(attempts=attempts, base_delay=1.0, max_delay=60.0, sleep=waits.append)


def test_retries_429_honouring_retry_after():
    client, calls = _sequence(httpx.Response(429, headers={"Retry-After": "7"}), httpx.Response(200, json={}))
    waits: list[float] = []
    response = request(client, "GET", "https://api.example.com/x", retry=_policy(waits))
    assert response.status_code == 200
    assert waits == [7.0]
    assert len(calls) == 2


def test_retries_5xx_with_exponential_backoff_then_gives_up():
    client, calls = _sequence(*(httpx.Response(503) for _ in range(4)))
    waits: list[float] = []
    response = request(client, "GET", "https://api.example.com/x", retry=_policy(waits))
    assert response.status_code == 503
    assert waits == [1.0, 2.0, 4.0]
    assert len(calls) == 4


def test_retry_after_is_capped():
    client, _ = _sequence(httpx.Response(429, headers={"Retry-After": "86400"}), httpx.Response(200))
    waits: list[float] = []
    request(client, "GET", "https://api.example.com/x", retry=_policy(waits))
    assert waits == [60.0]


def test_client_errors_are_not_retried():
    client, calls = _sequence(httpx.Response(404))
    waits: list[float] = []
    assert request(client, "GET", "https://api.example.com/x", retry=_policy(waits)).status_code == 404
    assert waits == [] and len(calls) == 1


def test_transport_errors_are_retried_then_raised():
    client, calls = _sequence(httpx.ConnectError("down"), httpx.ConnectError("still down"))
    waits: list[float] = []
    with pytest.raises(httpx.ConnectError):
        request(client, "GET", "https://api.example.com/x", retry=_policy(waits, attempts=2))
    assert len(calls) == 2 and waits == [1.0]


def test_retry_after_http_date():
    when = datetime.now(timezone.utc) + timedelta(seconds=30)
    seconds = retry_after_seconds(format_datetime(when, usegmt=True))
    assert 25 <= seconds <= 31
    assert retry_after_seconds("garbage") is None
    assert retry_after_seconds(None) is None


@pytest.mark.parametrize(
    "value",
    ["2024-01-15T10:30:00.000Z", "2024-01-15T11:30:00.000+01:00", "2024-01-15T11:30:00.000+0100"],
)
def test_parse_timestamp_atlassian_flavours(value):
    assert parse_timestamp(value) == datetime(2024, 1, 15, 10, 30, tzinfo=timezone.utc).timestamp()


def test_parse_timestamp_rejects_garbage():
    assert parse_timestamp("yesterday") is None
    assert parse_timestamp(None) is None


def test_parse_http_date():
    assert parse_http_date("Mon, 15 Jan 2024 10:30:00 GMT") == datetime(
        2024, 1, 15, 10, 30, tzinfo=timezone.utc
    ).timestamp()
    assert parse_http_date("not a date") is None


def test_make_client_keeps_proxy_and_ca_env_support():
    with make_client(headers={"X-Test": "1"}) as client:
        assert client.trust_env is True
        assert client.headers["X-Test"] == "1"
        assert client.headers["User-Agent"].startswith("mnemos-connectors/")
        assert client.follow_redirects is True
