"""`config/connectors.yaml`: which Confluence spaces, Jira projects and web
sites to keep in sync, read by `mnemos ingest run` and `mnemos ingest serve`.

Secrets never live in the file: an Atlassian source names the environment
variable holding its token (`token_env`), resolved at load time so a missing
credential fails before anything is synced.
"""

from __future__ import annotations

import os
import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

import yaml

from connectors.web import group_seeds

DEFAULT_MAX_PAGES = 200
_DEPLOYMENTS = ("auto", "cloud", "datacenter")
_INTERVAL = re.compile(r"^(\d+)\s*([smhd]?)$")
_UNIT_SECONDS = {"": 1, "s": 1, "m": 60, "h": 3600, "d": 86400}


class ConfigError(ValueError):
    """The connectors file cannot be used; the message lists every problem."""


@dataclass(frozen=True)
class ConfluenceConfig:
    name: str
    url: str
    token: str
    user: str | None
    deployment: str
    spaces: tuple[str, ...]
    tags: tuple[str, ...]
    type: str = "confluence"


@dataclass(frozen=True)
class JiraConfig:
    name: str
    url: str
    token: str
    user: str | None
    deployment: str
    projects: tuple[str, ...]
    jql: str | None
    tags: tuple[str, ...]
    type: str = "jira"


@dataclass(frozen=True)
class WebConfig:
    name: str
    urls: tuple[str, ...]
    max_pages: int
    tags: tuple[str, ...]
    type: str = "web"


SourceConfig = ConfluenceConfig | JiraConfig | WebConfig


@dataclass(frozen=True)
class ConnectorsConfig:
    interval: float | None  # seconds between two `serve` runs; None = start-up and SIGHUP only
    sources: tuple[SourceConfig, ...]


def parse_interval(value: object) -> int:
    """`45s`, `30m`, `6h`, `1d` or a number of seconds → seconds (> 0)."""
    if isinstance(value, bool):
        raise ValueError(f"invalid interval {value!r}")
    match = _INTERVAL.match(str(value).strip())
    if not match or int(match.group(1)) == 0:
        raise ValueError(f"invalid interval {value!r} (expected e.g. 30m, 6h, 1d)")
    return int(match.group(1)) * _UNIT_SECONDS[match.group(2)]


_COMMON_KEYS = {"type", "name", "tags"}
_KEYS = {
    "confluence": _COMMON_KEYS | {"url", "user", "token_env", "deployment", "spaces"},
    "jira": _COMMON_KEYS | {"url", "user", "token_env", "deployment", "projects", "jql"},
    "web": _COMMON_KEYS | {"urls", "max_pages"},
}


def load_config(path: Path | str, environ: Mapping[str, str] = os.environ) -> ConnectorsConfig:
    """Parse and validate the file; raise ConfigError listing every problem."""
    path = Path(path)
    if not path.is_file():
        raise ConfigError(f"{path}: not found")
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:
        raise ConfigError(f"{path}: invalid YAML: {exc}") from exc
    if not isinstance(raw, dict):
        raise ConfigError(f"{path}: expected a mapping with `sources:`")

    problems: list[str] = [f"unknown key {k!r}" for k in raw if k not in ("interval", "sources")]

    interval = None
    if raw.get("interval") is not None:
        try:
            interval = parse_interval(raw["interval"])
        except ValueError as exc:
            problems.append(str(exc))

    entries = raw.get("sources")
    if not isinstance(entries, list) or not entries:
        problems.append("no source declared under `sources:`")
        entries = []

    sources: list[SourceConfig] = []
    for index, entry in enumerate(entries):
        source = _parse_source(index, entry, environ, problems)
        if source is not None:
            sources.append(source)

    seen: set[str] = set()
    for source in sources:
        if source.name in seen:
            problems.append(f"duplicate source name {source.name!r}: set a distinct `name`")
        seen.add(source.name)

    if problems:
        raise ConfigError(f"{path}:\n" + "\n".join(f"  - {p}" for p in problems))
    return ConnectorsConfig(interval=interval, sources=tuple(sources))


def _parse_source(
    index: int, entry: object, environ: Mapping[str, str], problems: list[str]
) -> SourceConfig | None:
    if not isinstance(entry, dict):
        problems.append(f"sources[{index}]: expected a mapping")
        return None
    kind = entry.get("type")
    if kind not in _KEYS:
        problems.append(f"sources[{index}]: unknown type {kind!r} (expected confluence, jira or web)")
        return None

    name = str(entry.get("name") or kind)
    where = f"sources[{index}] ({name})"
    before = len(problems)
    problems.extend(f"{where}: unknown key {k!r}" for k in entry if k not in _KEYS[kind])

    def strings(key: str, *, required: bool) -> tuple[str, ...]:
        value = entry.get(key)
        if value is None:
            if required:
                problems.append(f"{where}: missing {key!r}")
            return ()
        items = value if isinstance(value, list) else None
        if not items or not all(isinstance(v, (str, int)) and str(v).strip() for v in items):
            problems.append(f"{where}: {key!r} must be a non-empty list of strings")
            return ()
        return tuple(str(v).strip() for v in items)

    tags = strings("tags", required=False)

    if kind == "web":
        urls = strings("urls", required=True)
        try:
            group_seeds(urls)
        except ValueError as exc:
            problems.append(f"{where}: {exc}")
        max_pages = entry.get("max_pages", DEFAULT_MAX_PAGES)
        if isinstance(max_pages, bool) or not isinstance(max_pages, int) or max_pages < 1:
            problems.append(f"{where}: 'max_pages' must be a positive integer")
        if len(problems) > before:
            return None
        return WebConfig(name=name, urls=urls, max_pages=max_pages, tags=tags)

    url = entry.get("url")
    if not isinstance(url, str) or not url.startswith(("http://", "https://")):
        problems.append(f"{where}: 'url' must be an http(s) URL")
    deployment = entry.get("deployment", "auto")
    if deployment not in _DEPLOYMENTS:
        problems.append(f"{where}: 'deployment' must be one of {', '.join(_DEPLOYMENTS)}")
    token = None
    token_env = entry.get("token_env")
    if not isinstance(token_env, str) or not token_env:
        problems.append(f"{where}: missing 'token_env' (name of the variable holding the token)")
    else:
        token = environ.get(token_env, "").strip()
        if not token:
            problems.append(f"{where}: environment variable {token_env} is not set")
    user = entry.get("user")
    if user is not None and not isinstance(user, str):
        problems.append(f"{where}: 'user' must be a string")

    if kind == "confluence":
        spaces = strings("spaces", required=True)
        if len(problems) > before:
            return None
        return ConfluenceConfig(
            name=name, url=url, token=token, user=user, deployment=deployment,
            spaces=spaces, tags=tags,
        )

    projects = strings("projects", required=True)
    jql = entry.get("jql")
    if jql is not None and not isinstance(jql, str):
        problems.append(f"{where}: 'jql' must be a string")
    if len(problems) > before:
        return None
    return JiraConfig(
        name=name, url=url, token=token, user=user, deployment=deployment,
        projects=projects, jql=jql, tags=tags,
    )
