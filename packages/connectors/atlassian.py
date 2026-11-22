"""Bits shared by the Confluence and Jira connectors."""

from __future__ import annotations

from typing import Literal
from urllib.parse import urlsplit

import httpx

Deployment = Literal["cloud", "datacenter"]

# Atlassian Cloud sites live on these domains; anything else is a
# self-hosted Server / Data Center instance.
_CLOUD_SUFFIXES = (".atlassian.net", ".jira.com", ".jira-dev.com")


def detect_deployment(url: str, explicit: str = "auto") -> Deployment:
    """``cloud`` or ``datacenter``: the explicit choice wins, else the host decides.

    Cloud sites are recognised by their ``*.atlassian.net`` host. A Cloud
    site behind a custom domain must pass ``--deployment cloud``.
    """
    if explicit in ("cloud", "datacenter"):
        return explicit  # type: ignore[return-value]
    if explicit != "auto":
        raise ValueError(f"Unknown deployment {explicit!r} (expected auto, cloud or datacenter)")
    host = (urlsplit(url).hostname or "").lower()
    return "cloud" if host.endswith(_CLOUD_SUFFIXES) else "datacenter"


def auth_kwargs(user: str | None, token: str) -> dict:
    """``httpx.Client`` kwargs for an Atlassian token.

    * user + token → HTTP Basic (Cloud: account email + API token);
    * token alone  → ``Authorization: Bearer`` (Data Center personal access token).
    """
    if not token:
        raise ValueError("An API token is required")
    if user:
        return {"auth": httpx.BasicAuth(user, token)}
    return {"headers": {"Authorization": f"Bearer {token}"}}


def site_root(url: str, *, strip_suffix: str = "") -> str:
    """Normalise a user-supplied base URL: no trailing slash, optional suffix removed."""
    root = url.strip().rstrip("/")
    if strip_suffix and root.endswith(strip_suffix):
        root = root[: -len(strip_suffix)]
    return root
