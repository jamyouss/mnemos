"""Run the sources of a connectors config: once (`mnemos ingest run`) or as a
long-lived service that syncs at start-up, then on a timer, on a config change
or on demand (`mnemos ingest serve`)."""

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


Report = Callable[[str, str, list[SyncResult]], object]


def run_sources(
    config: ConnectorsConfig,
    mnemos: MnemosClient,
    *,
    only: Collection[str] | None = None,
    full: bool = False,
    report: Report | None = None,
) -> list[SyncResult]:
    """Sync every scope of every selected source; one result per scope.

    A source that cannot even start (bad URL, unreachable host while listing…)
    yields a failed result and the run moves on to the next one. `report`
    hears `(name, "running", [])` before each source and `(name, "ok" |
    "failed", its results)` after; a report that raises is logged, never fatal.
    """
    results: list[SyncResult] = []
    for source in config.sources:
        if only and source.name not in only:
            continue
        _report(report, source.name, "running", [])
        try:
            done = _run_source(source, mnemos, full)
        except Exception as exc:  # noqa: BLE001 — reported per source, never fatal for the run
            logger.error("source %s (%s) failed: %s", source.name, source.type, exc)
            done = [SyncResult(scope_tag=f"{source.name} ({source.type})", error=str(exc))]
        _report(report, source.name, "ok" if all(r.ok for r in done) else "failed", done)
        results.extend(done)
    return results


def _report(report: Report | None, name: str, state: str, results: list[SyncResult]) -> None:
    if report is None:
        return
    try:
        report(name, state, results)
    except Exception as exc:  # noqa: BLE001 — status is best effort, the sync is what matters
        logger.warning("cannot report the sync of %s: %s", name, exc)


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


class SyncQueue:
    """Syncs waiting to run: every source, or some by name.

    Requests made while a run is going on merge into the next one, so a burst
    of "sync now" clicks costs one run, not one per click.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._everything = False
        self._names: set[str] = set()
        self.ready = threading.Event()

    def request(self, names: Collection[str] | None = None) -> None:
        """Queue `names`, or every source when None. An empty list queues nothing."""
        with self._lock:
            if names is None:
                self._everything = True
            elif names:
                self._names.update(names)
            else:
                return
            self.ready.set()

    def take(self) -> set[str] | None:
        """What to sync now: None for every source, else the names; empties the queue."""
        with self._lock:
            only = None if self._everything else set(self._names)
            self._everything = False
            self._names.clear()
            self.ready.clear()
            return only


def serve(
    run_once: Callable[[set[str] | None], object],
    *,
    interval: float | None,
    queue: SyncQueue,
    stop: threading.Event,
) -> None:
    """Sync every source now, then whatever `queue` asks for, and every source
    again whenever `interval` elapses with nothing asked.

    `run_once` gets None (every source) or the names to sync. `interval=None`
    waits for requests only. Setting `stop` (then `queue.ready`, to wake a
    pending wait) ends the loop. A run that raises is logged and the service
    keeps going: the next run gets another chance.
    """
    queue.request()
    while not stop.is_set():
        if not queue.ready.wait(timeout=interval):
            queue.request()
        if stop.is_set():
            return
        try:
            run_once(queue.take())
        except Exception:  # noqa: BLE001 — a service must survive a bad run
            logger.exception("sync run failed")


def watch_file(path: str, queue: SyncQueue, stop: threading.Event, *, poll: float = 5.0) -> None:
    """Queue every source each time `path` changes, until `stop` is set.

    Lets an edit sync within seconds rather than at the next `interval`. The
    inode is part of the signature: editors and config tooling replace files
    atomically, which may keep mtime and size.
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
            queue.request()


def latest_request(snapshot: dict) -> int:
    """Id of the newest sync request in a /api/connectors snapshot (0 when none)."""
    sync = snapshot.get("sync") or {}
    return max([int(sync.get("acked_id", 0))] + [int(r.get("id", 0)) for r in sync.get("requests") or []])


def watch_server(
    read_snapshot: Callable[[], dict],
    revision: int,
    seen: int,
    queue: SyncQueue,
    stop: threading.Event,
    *,
    ack: Callable[[int], object],
    poll: float = 5.0,
) -> None:
    """Follow the server's /api/connectors snapshot until `stop` is set.

    A new revision (a dashboard save) queues every source. Each sync request
    newer than `seen` queues the sources it names. Requests up to the newest
    seen, `seen` included, are then acknowledged so the dashboard can tell
    they were picked up. A read that raises is logged and retried at the next
    poll: an unreachable server causes no resync.
    """
    acked = 0
    while not stop.wait(poll):
        try:
            snapshot = read_snapshot()
        except Exception as exc:  # noqa: BLE001 — the watcher must outlive a server outage
            logger.warning("cannot read the connectors: %s", exc)
            continue
        if snapshot["revision"] != revision:
            logger.info("connectors revision %s -> %s: resyncing", revision, snapshot["revision"])
            revision = snapshot["revision"]
            queue.request()
        for entry in (snapshot.get("sync") or {}).get("requests") or []:
            if int(entry.get("id", 0)) > seen:
                logger.info("sync request %s from %s: %s", entry["id"], entry.get("requested_by"),
                            ", ".join(entry["sources"]) if entry.get("sources") else "every source")
                queue.request(entry.get("sources"))
        seen = max(seen, latest_request(snapshot))
        if acked < seen:
            try:
                ack(seen)
                acked = seen
            except Exception as exc:  # noqa: BLE001 — retried at the next poll
                logger.warning("cannot acknowledge sync request %s: %s", seen, exc)


def keep_alive(beat: Callable[[], object], stop: threading.Event, *, every: float = 60.0) -> None:
    """Call `beat` now, then every `every` seconds until `stop` is set: the
    dashboard tells a stopped sync service from an idle one by it. A beat that
    raises is logged and the loop goes on."""
    while True:
        try:
            beat()
        except Exception as exc:  # noqa: BLE001 — liveness is best effort
            logger.warning("cannot report to the server: %s", exc)
        if stop.wait(every):
            return
