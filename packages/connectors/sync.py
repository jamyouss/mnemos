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
  server embeds many files in one pass; an older server gets them one by one.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Iterable
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
) -> SyncResult:
    """Mirror ``items`` into Mnemos under the ``<source>:<scope>`` tag."""
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
    try:
        for item in items:
            if item.file_path in seen:
                continue
            seen.add(item.file_path)
            _sync_item(mnemos, item, indexed, full, result, pusher)
        listing_complete = True
    except ListingTruncated as exc:
        result.truncated = True
        logger.warning("[%s] listing truncated (%s): nothing deleted", scope_tag, exc)
    except Exception as exc:  # noqa: BLE001 — any source failure means "do not delete"
        result.error = f"source listing failed: {exc}"
        logger.error("[%s] source listing failed: %s — nothing deleted", scope_tag, exc)
    # What was rendered is pushed even when the listing stopped early.
    pusher.flush()

    if listing_complete:
        for file_path in sorted(set(indexed) - seen):
            _delete(mnemos, file_path, result)

    logger.info(
        "[%s] pushed=%d unchanged=%d deleted=%d skipped=%d failed=%d",
        scope_tag, result.pushed, result.unchanged, result.deleted, result.skipped, result.failed,
    )
    return result


def _sync_item(
    mnemos: MnemosClient,
    item: SourceItem,
    indexed: dict[str, float | None],
    full: bool,
    result: SyncResult,
    pusher: _Pusher,
) -> None:
    if not needs_push(
        item.updated, indexed.get(item.file_path), indexed=item.file_path in indexed, full=full
    ):
        result.unchanged += 1
        return
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
    """Collects rendered documents and pushes them a batch at a time."""

    def __init__(self, mnemos: MnemosClient, tags: list[str], result: SyncResult) -> None:
        self._mnemos = mnemos
        self._tags = tags
        self._result = result
        self._pending: list[tuple[SourceItem, dict]] = []
        self._chars = 0
        self._batched = True

    def add(self, item: SourceItem, content: str) -> None:
        self._pending.append((item, {
            "file_path": item.file_path, "content": content, "tags": self._tags,
            "source_url": item.source_url, "file_mtime": item.updated,
        }))
        self._chars += len(content)
        if len(self._pending) >= BATCH_FILES or self._chars >= BATCH_CHARS:
            self.flush()

    def flush(self) -> None:
        pending, self._pending, self._chars = self._pending, [], 0
        if not pending:
            return
        if self._batched:
            try:
                answers = self._mnemos.push_many([_without_none(doc) for _, doc in pending])
            except Exception as exc:  # noqa: BLE001 — the batch fails, the run goes on
                for item, _ in pending:
                    self._failed(item, str(exc))
                return
            if answers is not None:
                for (item, _), answer in zip(pending, answers):
                    if answer.get("status") in ("indexed", "skipped"):
                        self._result.pushed += 1
                    else:
                        self._failed(item, answer.get("error") or answer.get("status"))
                return
            self._batched = False  # an older server: one request per document from now on
        for item, doc in pending:
            try:
                self._mnemos.push(**doc)
            except Exception as exc:  # noqa: BLE001 — one bad document must not stop the run
                self._failed(item, str(exc))
            else:
                self._result.pushed += 1

    def _failed(self, item: SourceItem, error: str | None) -> None:
        self._result.failed += 1
        logger.error("[%s] push %s (%s) failed: %s", self._result.scope_tag, item.file_path, item.title, error)


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
