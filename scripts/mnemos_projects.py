"""The server's projects mapping, for the host-side scripts.

`GET /api/projects` on `MNEMOS_URL` (default http://localhost:8100), with
`MNEMOS_API_KEY` as bearer when set — the key needs `admin` or write on every
tag. The endpoint answers rows; they are turned back into the stored
`{"paths": {prefix: {...}}}` form so `core.projects.path_tags_from` /
`path_excludes_from` read them exactly as the server does.
"""
from __future__ import annotations

import json
import os
from urllib import request

DEFAULT_MNEMOS_URL = "http://localhost:8100"


class ProjectsUnavailable(RuntimeError):
    """The server could not hand out its projects mapping."""


def mnemos_url() -> str:
    return os.environ.get("MNEMOS_URL", DEFAULT_MNEMOS_URL).rstrip("/")


def auth_headers() -> dict[str, str]:
    """Bearer header for servers running with MNEMOS_AUTH_ENABLED=true."""
    key = os.environ.get("MNEMOS_API_KEY", "").strip()
    return {"Authorization": f"Bearer {key}"} if key else {}


def fetch_projects(base_url: str, timeout: float = 10) -> dict:
    """The projects mapping in its stored form; ProjectsUnavailable on any failure."""
    url = f"{base_url.rstrip('/')}/api/projects"
    try:
        with request.urlopen(request.Request(url, headers=auth_headers()), timeout=timeout) as resp:
            body = json.loads(resp.read().decode("utf-8"))
    except (OSError, ValueError) as exc:  # OSError covers URLError, HTTPError and timeouts
        raise ProjectsUnavailable(f"cannot read the projects from {url}: {exc}") from exc
    rows = body.get("paths") if isinstance(body, dict) else None
    if not isinstance(rows, list):
        raise ProjectsUnavailable(f"{url}: not a projects snapshot")
    return {
        "paths": {
            row["prefix"]: {
                "tags": row.get("tags") or [],
                "exclude_dirs": row.get("exclude_dirs") or [],
                "exclude_exts": row.get("exclude_exts") or [],
            }
            for row in rows
            if isinstance(row, dict) and isinstance(row.get("prefix"), str)
        }
    }
