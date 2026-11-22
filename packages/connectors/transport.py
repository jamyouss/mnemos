"""HTTP plumbing shared by every connector: client factory and retry policy.

Every request goes through :func:`request`, which retries throttling (429)
and transient server errors (5xx) with exponential backoff, honouring the
``Retry-After`` header when the server sends one. Clients are plain
``httpx.Client`` objects built with the default ``trust_env=True``, so
``HTTPS_PROXY`` / ``NO_PROXY`` / ``SSL_CERT_FILE`` work as usual.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

import httpx

logger = logging.getLogger(__name__)

USER_AGENT = "mnemos-connectors/0.1"

# Source APIs answer in well under a second; the generous read timeout covers
# big Confluence pages and slow intranet proxies.
SOURCE_TIMEOUT = httpx.Timeout(60.0, connect=10.0)

RETRY_STATUSES = frozenset({429, 500, 502, 503, 504})


@dataclass(frozen=True)
class RetryPolicy:
    """How hard to insist on a failing request.

    ``sleep`` is injectable so tests never wait for real.
    """

    attempts: int = 4
    base_delay: float = 1.0
    max_delay: float = 60.0
    sleep: Callable[[float], None] = field(default=time.sleep)

    def delay(self, attempt: int, response: httpx.Response | None) -> float:
        """Seconds to wait before retry number ``attempt`` (1-based)."""
        if response is not None:
            hinted = retry_after_seconds(response.headers.get("Retry-After"))
            if hinted is not None:
                return min(hinted, self.max_delay)
        return min(self.base_delay * (2 ** (attempt - 1)), self.max_delay)


DEFAULT_RETRY = RetryPolicy()


def retry_after_seconds(value: str | None) -> float | None:
    """Parse a ``Retry-After`` header: delta-seconds or an HTTP date."""
    if not value:
        return None
    value = value.strip()
    try:
        return max(0.0, float(value))
    except ValueError:
        pass
    try:
        when = parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    return max(0.0, (when - datetime.now(timezone.utc)).total_seconds())


def request(
    client: httpx.Client,
    method: str,
    url: str,
    *,
    retry: RetryPolicy = DEFAULT_RETRY,
    **kwargs,
) -> httpx.Response:
    """Send a request, retrying 429/5xx and transport errors.

    Returns the last response whatever its status — callers decide what a
    404 means. A transport error that survives every attempt is re-raised.
    """
    attempt = 1
    while True:
        try:
            response = client.request(method, url, **kwargs)
        except httpx.TransportError as exc:
            if attempt >= retry.attempts:
                raise
            wait = retry.delay(attempt, None)
            logger.warning("%s %s failed (%s), retrying in %.1fs", method, url, exc, wait)
        else:
            if response.status_code not in RETRY_STATUSES or attempt >= retry.attempts:
                return response
            wait = retry.delay(attempt, response)
            logger.warning(
                "%s %s returned %s, retrying in %.1fs", method, url, response.status_code, wait
            )
        retry.sleep(wait)
        attempt += 1


def get_json(
    client: httpx.Client,
    url: str,
    *,
    retry: RetryPolicy = DEFAULT_RETRY,
    params: dict | None = None,
) -> dict:
    """GET ``url`` and decode its JSON body, raising on a non-2xx answer."""
    response = request(client, "GET", url, retry=retry, params=params)
    response.raise_for_status()
    return response.json()


def make_client(
    *,
    auth: httpx.Auth | None = None,
    headers: dict[str, str] | None = None,
    timeout: httpx.Timeout | float = SOURCE_TIMEOUT,
) -> httpx.Client:
    """Build the ``httpx.Client`` used by the CLI.

    Kept as the single construction point so tests can swap it for a client
    on an ``httpx.MockTransport`` and exercise a command end to end.
    """
    return httpx.Client(
        auth=auth,
        headers={"User-Agent": USER_AGENT, **(headers or {})},
        timeout=timeout,
        follow_redirects=True,
    )


def parse_timestamp(value: str | None) -> float | None:
    """ISO-8601 timestamp (Atlassian flavours included) → epoch seconds.

    Accepts ``2024-01-15T10:30:00.000Z``, ``...+01:00`` and Jira's
    ``...+0100``. Returns None for empty or unparseable values so the sync
    engine re-pushes rather than trusting a bogus date.
    """
    if not value:
        return None
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    elif len(text) > 5 and text[-5] in "+-" and text[-4:].isdigit():
        text = f"{text[:-2]}:{text[-2:]}"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        logger.debug("Unparseable timestamp %r", value)
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.timestamp()


def parse_http_date(value: str | None) -> float | None:
    """``Last-Modified`` style HTTP date → epoch seconds (None if absent/bad)."""
    if not value:
        return None
    try:
        parsed = parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.timestamp()


def iso_date(epoch: float | None) -> str:
    """Epoch seconds → ``YYYY-MM-DD HH:MM UTC`` for metadata lines."""
    if epoch is None:
        return ""
    return datetime.fromtimestamp(epoch, timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
