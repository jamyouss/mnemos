from __future__ import annotations

from collections.abc import Iterator

import httpx

from connectors.mnemos import MnemosClient
from connectors.sync import (
    ListingTruncated,
    SourceError,
    SourceItem,
    needs_push,
    scope_tags,
    sync_scope,
)

from .conftest import MNEMOS_URL, NO_WAIT, FakeMnemos

TAGS = ["team", "confluence", "confluence:ENG"]


def _item(page_id: str, updated: float | None, body: str = "body") -> SourceItem:
    return SourceItem(
        file_path=f"confluence/ENG/{page_id}.md",
        title=f"Page {page_id}",
        source_url=f"https://wiki.example.com/pages/{page_id}",
        updated=updated,
        content=f"# Page {page_id}\n\n{body}",
    )


def _sync(mnemos: FakeMnemos, items, full: bool = False):
    return sync_scope(
        mnemos.client(), source="confluence", scope="ENG", items=items, user_tags=["team"], full=full
    )


def test_scope_tags_order_and_dedup():
    assert scope_tags("jira", "OPS", ["a", "jira", "a"]) == ["a", "jira", "jira:OPS"]


def test_needs_push_rules():
    assert needs_push(10.0, None, indexed=False, full=False)
    assert needs_push(10.0, 10.0, indexed=True, full=True)
    assert needs_push(None, 10.0, indexed=True, full=False)
    assert needs_push(11.0, 10.0, indexed=True, full=False)
    assert not needs_push(10.0, 10.0, indexed=True, full=False)


def test_pushes_new_skips_unchanged_pushes_changed(mnemos):
    mnemos.seed("confluence/ENG/1.md", 100.0, TAGS)
    mnemos.seed("confluence/ENG/2.md", 100.0, TAGS)

    result = _sync(mnemos, [_item("1", 100.0), _item("2", 200.0), _item("3", 50.0)])

    assert (result.pushed, result.unchanged, result.deleted, result.failed) == (2, 1, 0, 0)
    assert result.ok
    assert mnemos.pushed_paths == ["confluence/ENG/2.md", "confluence/ENG/3.md"]
    pushed = mnemos.pushed[0]
    assert pushed["collection"] == "mnemos_knowledge"
    assert pushed["tags"] == TAGS
    assert pushed["file_mtime"] == 200.0
    assert pushed["source_url"] == "https://wiki.example.com/pages/2"
    assert pushed["content"].startswith("# Page 2")


def test_listing_uses_the_scope_tag(mnemos):
    _sync(mnemos, [])
    listing = mnemos.requests[0]
    assert listing.url.path == "/api/index/mnemos_knowledge/files"
    assert listing.url.params["tag"] == "confluence:ENG"


def test_item_without_date_is_always_pushed(mnemos):
    mnemos.seed("confluence/ENG/1.md", 100.0, TAGS)
    result = _sync(mnemos, [_item("1", None)])
    assert result.pushed == 1
    assert "file_mtime" not in mnemos.pushed[0]


def test_full_pushes_everything(mnemos):
    mnemos.seed("confluence/ENG/1.md", 100.0, TAGS)
    result = _sync(mnemos, [_item("1", 100.0)], full=True)
    assert (result.pushed, result.unchanged) == (1, 0)


def test_deletes_files_not_seen_after_a_complete_listing(mnemos):
    mnemos.seed("confluence/ENG/1.md", 100.0, TAGS)
    mnemos.seed("confluence/ENG/gone.md", 100.0, TAGS)
    mnemos.seed("confluence/OTHER/9.md", 100.0, ["confluence", "confluence:OTHER"])

    result = _sync(mnemos, [_item("1", 100.0)])

    assert result.deleted == 1
    assert mnemos.deleted == ["confluence/ENG/gone.md"]
    assert "confluence/OTHER/9.md" in mnemos.files  # other scopes untouched


def test_failed_listing_never_deletes(mnemos):
    mnemos.seed("confluence/ENG/1.md", 100.0, TAGS)
    mnemos.seed("confluence/ENG/2.md", 100.0, TAGS)

    def broken() -> Iterator[SourceItem]:
        yield _item("1", 300.0)
        raise httpx.ConnectError("source went away")

    result = _sync(mnemos, broken())

    assert result.pushed == 1  # what was listed before the failure still lands
    assert result.deleted == 0
    assert mnemos.deleted == []
    assert not result.ok
    assert "source went away" in result.error


def test_source_error_at_the_end_still_blocks_deletes(mnemos):
    mnemos.seed("confluence/ENG/old.md", 100.0, TAGS)

    def partial() -> Iterator[SourceItem]:
        yield _item("1", 1.0)
        raise SourceError("2 page(s) could not be fetched")

    result = _sync(mnemos, partial())
    assert mnemos.deleted == []
    assert result.error and not result.ok


def test_truncated_listing_pushes_but_does_not_delete(mnemos):
    mnemos.seed("confluence/ENG/old.md", 100.0, TAGS)

    def capped() -> Iterator[SourceItem]:
        yield _item("1", 1.0)
        raise ListingTruncated("max pages")

    result = _sync(mnemos, capped())
    assert result.pushed == 1
    assert result.truncated
    assert result.ok
    assert mnemos.deleted == []


def test_one_failed_push_does_not_abort_the_run(mnemos):
    mnemos.fail_push_for.add("confluence/ENG/2.md")
    mnemos.seed("confluence/ENG/gone.md", 100.0, TAGS)

    result = _sync(mnemos, [_item("1", 1.0), _item("2", 1.0), _item("3", 1.0)])

    assert (result.pushed, result.failed, result.deleted) == (2, 1, 1)
    assert not result.ok
    # A failed push is still "seen": it must not be deleted.
    assert "confluence/ENG/2.md" not in mnemos.deleted


def test_render_failure_counts_as_failed_push(mnemos):
    def boom() -> str:
        raise httpx.ReadTimeout("body too slow")

    item = SourceItem("confluence/ENG/1.md", "Page 1", "https://x", 1.0, boom)
    result = _sync(mnemos, [item, _item("2", 1.0)])
    assert (result.pushed, result.failed) == (1, 1)


def test_lazy_content_is_not_rendered_for_unchanged_items(mnemos):
    mnemos.seed("confluence/ENG/1.md", 100.0, TAGS)
    calls: list[str] = []

    def render() -> str:
        calls.append("rendered")
        return "# x"

    _sync(mnemos, [SourceItem("confluence/ENG/1.md", "Page 1", "https://x", 100.0, render)])
    assert calls == []


def test_duplicate_file_paths_are_pushed_once(mnemos):
    result = _sync(mnemos, [_item("1", 1.0), _item("1", 1.0)])
    assert result.pushed == 1


def test_unreachable_mnemos_is_reported_without_touching_the_source():
    def down(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused")

    client = MnemosClient(MNEMOS_URL, httpx.Client(transport=httpx.MockTransport(down)), retry=NO_WAIT)
    consumed: list[int] = []

    def items() -> Iterator[SourceItem]:
        consumed.append(1)
        yield _item("1", 1.0)

    result = sync_scope(client, source="confluence", scope="ENG", items=items())
    assert result.error and "cannot list" in result.error
    assert consumed == []


def test_a_skipped_item_is_counted_and_its_stale_copy_removed(mnemos):
    from connectors.sync import SkipItem

    mnemos.seed("confluence/ENG/1.md", 100.0, TAGS)

    def too_big() -> str:
        raise SkipItem("1.md: too big")

    item = SourceItem(file_path="confluence/ENG/1.md", title="1", source_url="u", updated=200.0, content=too_big)
    result = _sync(mnemos, [item])

    assert (result.skipped, result.deleted, result.pushed) == (1, 1, 0)
    assert result.ok
    assert mnemos.deleted == ["confluence/ENG/1.md"]


def _posts(mnemos: FakeMnemos) -> list[str]:
    return [r.url.path for r in mnemos.requests if r.method == "POST"]


def test_documents_are_pushed_in_batches(mnemos):
    from connectors.sync import BATCH_FILES

    result = _sync(mnemos, [_item(str(i), 1.0) for i in range(BATCH_FILES + 5)])

    assert result.pushed == BATCH_FILES + 5 and result.ok
    assert _posts(mnemos) == ["/api/index/batch", "/api/index/batch"]
    assert mnemos.pushed[0]["file_mtime"] == 1.0 and mnemos.pushed[0]["tags"] == TAGS


def test_an_older_server_gets_one_request_per_document(mnemos):
    mnemos.batch_route = False
    mnemos.fail_push_for.add("confluence/ENG/2.md")

    result = _sync(mnemos, [_item(str(i), 1.0) for i in range(4)])

    assert (result.pushed, result.failed) == (3, 1)
    # One probe of the batch route, then single pushes only.
    assert _posts(mnemos) == ["/api/index/batch"] + ["/api/index"] * 4


def test_a_failed_batch_counts_every_document_and_the_run_goes_on(mnemos, monkeypatch):
    calls = []

    def broken(self, docs):
        calls.append(len(docs))
        raise httpx.ConnectError("down")

    monkeypatch.setattr(MnemosClient, "push_many", broken)
    mnemos.seed("confluence/ENG/gone.md", 100.0, TAGS)

    result = _sync(mnemos, [_item("1", 1.0), _item("2", 1.0)])

    assert (result.pushed, result.failed, result.deleted) == (0, 2, 1)
    assert calls == [2]
