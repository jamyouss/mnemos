"""Source-agnostic sync engine.

A connector turns one *scope* (a Confluence space, a Jira project, a web
host) into an iterator of :class:`SourceItem`. :func:`sync_scope` compares it
with what Mnemos already holds under the scope tag and pushes, skips or
deletes accordingly. Mnemos is the only state: nothing is stored locally, so
a cron job in a throwaway container is enough.

Rules:

* push when the file is not indexed yet, when ``full`` is set, when the
  source gives no last-modified date, or when that date differs from the
  stored ``file_mtime``;
* delete indexed files the run did not see — **only** once the source
  listing finished without error. A failed or truncated listing never
  deletes anything: "not seen" would then mean "not reached", not "gone";
* one failed push (or render) is counted and logged, never fatal;
* documents to push are sent in batches (``/api/index/batch``), so the
  server embeds many files in one pass, while the next batch is read from
  the source; an older server gets them one by one.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Iterable
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass

from connectors.mnemos import MnemosClient

logger = logging.getLogger(__name__)

# A batch closes at whichever comes first. Characters bound one request's
# embedding time: ~150 chunks, about a minute on a 6-core CPU server, far
# under MNEMOS_TIMEOUT (a timed-out batch is retried, embedded all over again).
BATCH_FILES = 32
BATCH_CHARS = 200_000


class SourceError(RuntimeError):
    """The source listing failed or is known to be incomplete."""


class ListingTruncated(Exception):
    """The source stopped on purpose before the end (e.g. ``--max-pages``).

    Not a failure: what was listed is pushed, but nothing is deleted since
    unseen files may simply lie beyond the cut.
    """


class SkipItem(Exception):
    """Raised by a document's content callable: leave it out of the index
    (too large, binary…). An already indexed copy is removed."""


@dataclass(frozen=True)
class SourceItem:
    """One document of a source.

    ``content`` is either the Markdown itself or a callable producing it —
    connectors that need an extra request per document (page body, extra
    comments) pass a callable so unchanged documents cost nothing.
    """

    file_path: str
    title: str
    source_url: str
    updated: float | None
    content: str | Callable[[], str]

    def markdown(self) -> str:
        return self.content() if callable(self.content) else self.content


@dataclass
class SyncResult:
    scope_tag: str
    pushed: int = 0
    unchanged: int = 0
    deleted: int = 0
    failed: int = 0
    skipped: int = 0
    truncated: bool = False
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.failed == 0 and self.error is None


def scope_tags(source: str, scope: str, user_tags: Iterable[str] = ()) -> list[str]:
    """``[*user_tags, "<source>", "<source>:<scope>"]`` without duplicates."""
    tags: list[str] = []
    for tag in (*user_tags, source, f"{source}:{scope}"):
        if tag and tag not in tags:
            tags.append(tag)
    return tags


def needs_push(updated: float | None, stored: float | None, *, indexed: bool, full: bool) -> bool:
    if full or not indexed or updated is None or stored is None:
        return True
    return float(updated) != float(stored)


def sync_scope(
    mnemos: MnemosClient,
    *,
    source: str,
    scope: str,
    items: Iterable[SourceItem],
    user_tags: Iterable[str] = (),
    full: bool = False,
    prefetch: Callable[[list[SourceItem]], None] | None = None,
) -> SyncResult:
    """Mirror ``items`` into Mnemos under the ``<source>:<scope>`` tag.

    ``prefetch``, when the source has one, downloads the contents of the next
    items to push in one go, before they are rendered one by one. The source
    must keep contents readable after its listing ends (``GitSource`` does).
    """
    scope_tag = f"{source}:{scope}"
    tags = scope_tags(source, scope, user_tags)
    result = SyncResult(scope_tag=scope_tag)

    try:
        indexed = mnemos.list_files(scope_tag)
    except Exception as exc:  # noqa: BLE001 — reported, and nothing else is safe to do
        logger.error("[%s] cannot list indexed files: %s", scope_tag, exc)
        result.error = f"cannot list indexed files: {exc}"
        return result

    seen: set[str] = set()
    listing_complete = False
    pusher = _Pusher(mnemos, tags, result)
    window: list[SourceItem] = []

    def render_window() -> None:
        if prefetch is not None and window:
            try:
                prefetch(list(window))
            except Exception as exc:  # noqa: BLE001 — each content is then fetched on its own
                logger.warning("[%s] prefetch failed, contents fetched one by one: %s", scope_tag, exc)
        for pending in window:
            _render(mnemos, pending, indexed, result, pusher)
        window.clear()

    try:
        try:
            for item in items:
                if item.file_path in seen:
                    continue
                seen.add(item.file_path)
                if not needs_push(
                    item.updated, indexed.get(item.file_path), indexed=item.file_path in indexed, full=full
                ):
                    result.unchanged += 1
                elif prefetch is None:
                    _render(mnemos, item, indexed, result, pusher)
                else:
                    window.append(item)
                    if len(window) >= BATCH_FILES:
                        render_window()
            listing_complete = True
        except ListingTruncated as exc:
            result.truncated = True
            logger.warning("[%s] listing truncated (%s): nothing deleted", scope_tag, exc)
        except Exception as exc:  # noqa: BLE001 — any source failure means "do not delete"
            result.error = f"source listing failed: {exc}"
            logger.error("[%s] source listing failed: %s — nothing deleted", scope_tag, exc)
        # What was listed is pushed even when the listing stopped early.
        render_window()
        pusher.flush()
    finally:
        pusher.close()

    if listing_complete:
        for file_path in sorted(set(indexed) - seen):
            _delete(mnemos, file_path, result)

    logger.info(
        "[%s] pushed=%d unchanged=%d deleted=%d skipped=%d failed=%d",
        scope_tag, result.pushed, result.unchanged, result.deleted, result.skipped, result.failed,
    )
    return result


def _render(
    mnemos: MnemosClient,
    item: SourceItem,
    indexed: dict[str, float | None],
    result: SyncResult,
    pusher: _Pusher,
) -> None:
    try:
        content = item.markdown()
    except SkipItem as exc:
        result.skipped += 1
        logger.info("[%s] skipped %s", result.scope_tag, exc)
        # Seen, so the end-of-run sweep keeps it: drop a stale copy here.
        if item.file_path in indexed:
            _delete(mnemos, item.file_path, result)
        return
    except Exception as exc:  # noqa: BLE001 — one bad document must not stop the run
        result.failed += 1
        logger.error("[%s] render %s (%s) failed: %s", result.scope_tag, item.file_path, item.title, exc)
        return
    pusher.add(item, content)


class _Pusher:
    """Collects rendered documents and pushes them a batch at a time.

    One batch is in flight while the next one is read from the source, so the
    server embeds while the source is downloaded. Outcomes are tallied on the
    sync thread only, when the batch is waited for.
    """

    def __init__(self, mnemos: MnemosClient, tags: list[str], result: SyncResult) -> None:
        self._mnemos = mnemos
        self._tags = tags
        self._result = result
        self._pending: list[tuple[SourceItem, dict]] = []
        self._chars = 0
        self._batched = True  # touched by the push thread only
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="mnemos-push")
        self._in_flight: Future | None = None

    def add(self, item: SourceItem, content: str) -> None:
        self._pending.append((item, {
            "file_path": item.file_path, "content": content, "tags": self._tags,
            "source_url": item.source_url, "file_mtime": item.updated,
        }))
        self._chars += len(content)
        if len(self._pending) >= BATCH_FILES or self._chars >= BATCH_CHARS:
            self._submit()

    def flush(self) -> None:
        """Push what is pending and wait until every batch is answered."""
        self._submit()
        self._wait()

    def close(self) -> None:
        self._executor.shutdown(wait=True)

    def _submit(self) -> None:
        pending, self._pending, self._chars = self._pending, [], 0
        if not pending:
            return
        self._wait()  # one batch in flight at most: the server embeds one at a time anyway
        self._in_flight = self._executor.submit(self._send, pending)

    def _wait(self) -> None:
        if self._in_flight is None:
            return
        outcomes, self._in_flight = self._in_flight.result(), None
        for item, error in outcomes:
            if error is None:
                self._result.pushed += 1
            else:
                self._result.failed += 1
                logger.error("[%s] push %s (%s) failed: %s", self._result.scope_tag, item.file_path, item.title, error)

    def _send(self, pending: list[tuple[SourceItem, dict]]) -> list[tuple[SourceItem, str | None]]:
        """Push one batch; each document with its error, None when indexed. Never raises."""
        if self._batched:
            try:
                answers = self._mnemos.push_many([_without_none(doc) for _, doc in pending])
            except Exception as exc:  # noqa: BLE001 — the batch fails, the run goes on
                return [(item, str(exc)) for item, _ in pending]
            if answers is not None:
                return [
                    (item, None if answer.get("status") in ("indexed", "skipped")
                     else answer.get("error") or answer.get("status"))
                    for (item, _), answer in zip(pending, answers)
                ]
            self._batched = False  # an older server: one request per document from now on
        outcomes: list[tuple[SourceItem, str | None]] = []
        for item, doc in pending:
            try:
                self._mnemos.push(**doc)
            except Exception as exc:  # noqa: BLE001 — one bad document must not stop the run
                outcomes.append((item, str(exc)))
            else:
                outcomes.append((item, None))
        return outcomes


def _without_none(doc: dict) -> dict:
    return {k: v for k, v in doc.items() if v is not None}


def _delete(mnemos: MnemosClient, file_path: str, result: SyncResult) -> None:
    try:
        mnemos.delete(file_path)
    except Exception as exc:  # noqa: BLE001
        result.failed += 1
        logger.error("[%s] delete %s failed: %s", result.scope_tag, file_path, exc)
    else:
        result.deleted += 1
        logger.info("[%s] deleted %s", result.scope_tag, file_path)
