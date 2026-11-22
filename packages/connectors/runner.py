"""Run the sources of a connectors config: once (`mnemos ingest run`) or as a
long-lived service that syncs at start-up, then on a timer or on demand
(`mnemos ingest serve`)."""

from __future__ import annotations

import logging
import os
import threading
from collections.abc import Callable, Collection

from connectors import confluence, git, jira, transport, web
from connectors.atlassian import auth_kwargs
from connectors.config import ConfluenceConfig, ConnectorsConfig, GitConfig, SourceConfig, WebConfig
from connectors.mnemos import MnemosClient
from connectors.sync import SyncResult, sync_scope

logger = logging.getLogger(__name__)


def run_sources(
    config: ConnectorsConfig,
    mnemos: MnemosClient,
    *,
    only: Collection[str] | None = None,
    full: bool = False,
) -> list[SyncResult]:
    """Sync every scope of every selected source; one result per scope.

    A source that cannot even start (bad URL, unreachable host while listing…)
    yields a failed result and the run moves on to the next one.
    """
    results: list[SyncResult] = []
    for source in config.sources:
        if only and source.name not in only:
            continue
        try:
            results.extend(_run_source(source, mnemos, full))
        except Exception as exc:  # noqa: BLE001 — reported per source, never fatal for the run
            logger.error("source %s (%s) failed: %s", source.name, source.type, exc)
            results.append(SyncResult(scope_tag=f"{source.name} ({source.type})", error=str(exc)))
    return results


def _run_source(source: SourceConfig, mnemos: MnemosClient, full: bool) -> list[SyncResult]:
    if isinstance(source, GitConfig):
        scope = git.repo_scope(source.url)
        repo = git.GitSource(
            source.url, user=source.user, token=source.token, branch=source.branch,
            paths=source.paths, exclude=source.exclude, max_file_kb=source.max_file_kb,
        )
        return [
            sync_scope(mnemos.for_collection(source.collection), source=git.SOURCE, scope=scope,
                       items=repo.items(scope), user_tags=source.tags, full=full)
        ]

    if isinstance(source, WebConfig):
        with transport.make_client() as client:
            crawler = web.WebSource(client, max_pages=source.max_pages)
            return [
                sync_scope(mnemos, source=web.SOURCE, scope=scope, items=crawler.items(scope, seeds),
                           user_tags=source.tags, full=full)
                for scope, seeds in web.group_seeds(source.urls).items()
            ]

    with transport.make_client(**auth_kwargs(source.user, source.token)) as client:
        if isinstance(source, ConfluenceConfig):
            wiki = confluence.ConfluenceSource(client, source.url, deployment=source.deployment)
            return [
                sync_scope(mnemos, source=confluence.SOURCE, scope=confluence.scope_key(space),
                           items=wiki.items(space), user_tags=source.tags, full=full)
                for space in source.spaces
            ]

        tracker = jira.JiraSource(client, source.url, deployment=source.deployment)
        return [
            sync_scope(mnemos, source=jira.SOURCE, scope=jira.scope_key(project),
                       items=tracker.items(project, source.jql), user_tags=source.tags, full=full)
            for project in source.projects
        ]


def serve(
    run_once: Callable[[], object],
    *,
    interval: float | None,
    trigger: threading.Event,
    stop: threading.Event,
) -> None:
    """Call `run_once` now, then whenever `trigger` is set or `interval` elapses.

    `interval=None` waits for `trigger` only. Setting `stop` (with `trigger`, to
    wake a pending wait) ends the loop. A run that raises is logged and the
    service keeps going: the next run gets another chance.
    """
    while not stop.is_set():
        trigger.clear()
        try:
            run_once()
        except Exception:  # noqa: BLE001 — a service must survive a bad run
            logger.exception("sync run failed")
        if stop.is_set():
            return
        trigger.wait(timeout=interval)


def watch_file(path: str, trigger: threading.Event, stop: threading.Event, *, poll: float = 5.0) -> None:
    """Set `trigger` each time `path` changes, until `stop` is set.

    Lets an edit — by hand or from the dashboard — sync within seconds rather
    than at the next `interval`. The inode is part of the signature: the
    dashboard replaces the file atomically, which may keep mtime and size.
    """

    def signature() -> tuple[int, int, int] | None:
        try:
            st = os.stat(path)
        except OSError:
            return None
        return st.st_mtime_ns, st.st_size, st.st_ino

    last = signature()
    while not stop.wait(poll):
        current = signature()
        if current != last:
            last = current
            logger.info("%s changed: resyncing", path)
            trigger.set()
