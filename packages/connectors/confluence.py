"""Confluence connector — Cloud (REST v2) and Data Center (REST v1).

Endpoints:

* **Cloud** — ``GET /wiki/api/v2/spaces?keys=KEY`` (space id), then
  ``GET /wiki/api/v2/spaces/{id}/pages?status=current`` paginated by the
  cursor in ``_links.next`` / the ``Link`` header, then per changed page
  ``GET /wiki/api/v2/pages/{id}?body-format=storage&include-labels=true``.
  The v1 ``/wiki/rest/api/content`` listing is gone from the Cloud v1 spec.
* **Data Center** — ``GET /rest/api/content?spaceKey=KEY&type=page&status=current
  &expand=version,ancestors,metadata.labels`` paginated with ``start``/``limit``,
  then per changed page ``GET /rest/api/content/{id}?expand=body.storage``.

Bodies are fetched only for pages the sync engine decides to push, so an
incremental run over an unchanged space costs a handful of listing calls.
"""

from __future__ import annotations

import html
import logging
import re
from collections.abc import Iterator
from functools import partial
from urllib.parse import urljoin

import httpx
from bs4 import BeautifulSoup, Tag

from connectors.atlassian import detect_deployment, site_root
from connectors.document import html_to_markdown, render_document
from connectors.sync import SourceItem
from connectors.transport import DEFAULT_RETRY, RetryPolicy, get_json, iso_date, parse_timestamp, request

logger = logging.getLogger(__name__)

SOURCE = "confluence"

CLOUD_PAGE_LIMIT = 250  # v2 maximum
DC_PAGE_LIMIT = 100

_CDATA = re.compile(r"<!\[CDATA\[(.*?)\]\]>", re.S)

# Macros rendered as a quoted, labelled block.
_PANEL_MACROS = {"info": "Info", "note": "Note", "warning": "Warning", "tip": "Tip", "panel": ""}
# Macros whose plain-text body is code.
_CODE_MACROS = {"code", "noformat"}


def scope_key(space: str) -> str:
    """Space key as used in tags and paths.

    Global space keys are case-insensitive in Confluence: upper-casing them
    keeps the path segment clear of the server's lowercase deny-list
    (``dist``, ``build``, ``vendor``…). Personal spaces (``~user``) are kept.
    """
    key = space.strip()
    return key if key.startswith("~") else key.upper()


def file_path(space_key: str, page_id: str) -> str:
    return f"confluence/{space_key}/{page_id}.md"


class ConfluenceSource:
    """List the current pages of one space as :class:`SourceItem` objects."""

    source = SOURCE

    def __init__(
        self,
        client: httpx.Client,
        url: str,
        *,
        deployment: str = "auto",
        retry: RetryPolicy = DEFAULT_RETRY,
    ) -> None:
        self._client = client
        self._retry = retry
        self.deployment = detect_deployment(url, deployment)
        if self.deployment == "cloud":
            self._site = site_root(url, strip_suffix="/wiki")
            self._base = f"{self._site}/wiki"
        else:
            self._site = site_root(url)
            self._base = self._site

    def items(self, space: str) -> Iterator[SourceItem]:
        key = scope_key(space)
        if self.deployment == "cloud":
            yield from self._cloud_items(key)
        else:
            yield from self._dc_items(key)

    # -- Cloud (REST v2) ---------------------------------------------------

    def _cloud_items(self, key: str) -> Iterator[SourceItem]:
        space_id = self._cloud_space_id(key)
        # The whole (body-less) listing first: breadcrumbs come from parentId,
        # and a listing that fails half-way must not have pushed anything yet.
        pages = list(self._cloud_pages(space_id))
        by_id = {str(p["id"]): p for p in pages}
        logger.info("[confluence:%s] %d pages listed", key, len(pages))
        for page in pages:
            page_id = str(page["id"])
            ancestors = _cloud_ancestors(page, by_id)
            source_url = self._base + (page.get("_links") or {}).get("webui", "")
            updated = parse_timestamp((page.get("version") or {}).get("createdAt"))
            yield SourceItem(
                file_path=file_path(key, page_id),
                title=page.get("title") or page_id,
                source_url=source_url,
                updated=updated,
                content=partial(self._cloud_render, page_id, key, ancestors, source_url),
            )

    def _cloud_space_id(self, key: str) -> str:
        data = get_json(
            self._client,
            f"{self._base}/api/v2/spaces",
            params={"keys": key, "limit": 1},
            retry=self._retry,
        )
        results = data.get("results") or []
        if not results:
            raise LookupError(f"Confluence space {key!r} not found or not visible")
        return str(results[0]["id"])

    def _cloud_pages(self, space_id: str) -> Iterator[dict]:
        url: str | None = f"{self._base}/api/v2/spaces/{space_id}/pages"
        params: dict | None = {"status": "current", "limit": CLOUD_PAGE_LIMIT}
        while url:
            response = request(self._client, "GET", url, params=params, retry=self._retry)
            response.raise_for_status()
            data = response.json()
            yield from data.get("results") or []
            next_link = (data.get("_links") or {}).get("next") or response.links.get("next", {}).get("url")
            next_url = urljoin(self._site + "/", next_link) if next_link else None
            url = next_url if next_url != url else None
            params = None  # the cursor URL carries every parameter

    def _cloud_render(self, page_id: str, key: str, ancestors: list[str], source_url: str) -> str:
        data = get_json(
            self._client,
            f"{self._base}/api/v2/pages/{page_id}",
            params={"body-format": "storage", "include-labels": "true"},
            retry=self._retry,
        )
        body = ((data.get("body") or {}).get("storage") or {}).get("value") or ""
        labels = [lbl.get("name", "") for lbl in (data.get("labels") or {}).get("results") or []]
        updated = parse_timestamp((data.get("version") or {}).get("createdAt"))
        return _render(data.get("title") or page_id, source_url, key, ancestors, labels, updated, body)

    # -- Data Center (REST v1) ---------------------------------------------

    def _dc_items(self, key: str) -> Iterator[SourceItem]:
        url = f"{self._base}/rest/api/content"
        start = 0
        while True:
            data = get_json(
                self._client,
                url,
                params={
                    "spaceKey": key,
                    "type": "page",
                    "status": "current",
                    "expand": "version,ancestors,metadata.labels",
                    "start": start,
                    "limit": DC_PAGE_LIMIT,
                },
                retry=self._retry,
            )
            results = data.get("results") or []
            links = data.get("_links") or {}
            base = (links.get("base") or self._base).rstrip("/")
            for page in results:
                yield self._dc_item(page, key, base)
            if not results or "next" not in links:
                return
            start += len(results)

    def _dc_item(self, page: dict, key: str, base: str) -> SourceItem:
        page_id = str(page["id"])
        ancestors = [a.get("title", "") for a in page.get("ancestors") or [] if a.get("title")]
        labels = [
            lbl.get("name", "")
            for lbl in ((page.get("metadata") or {}).get("labels") or {}).get("results") or []
        ]
        source_url = base + (page.get("_links") or {}).get("webui", "")
        updated = parse_timestamp((page.get("version") or {}).get("when"))
        title = page.get("title") or page_id
        return SourceItem(
            file_path=file_path(key, page_id),
            title=title,
            source_url=source_url,
            updated=updated,
            content=partial(self._dc_render, page_id, title, key, ancestors, labels, source_url, updated),
        )

    def _dc_render(
        self,
        page_id: str,
        title: str,
        key: str,
        ancestors: list[str],
        labels: list[str],
        source_url: str,
        updated: float | None,
    ) -> str:
        data = get_json(
            self._client,
            f"{self._base}/rest/api/content/{page_id}",
            params={"expand": "body.storage"},
            retry=self._retry,
        )
        body = ((data.get("body") or {}).get("storage") or {}).get("value") or ""
        return _render(title, source_url, key, ancestors, labels, updated, body)


def _cloud_ancestors(page: dict, by_id: dict[str, dict]) -> list[str]:
    """Titles from the space root down to the parent, rebuilt from ``parentId``."""
    titles: list[str] = []
    seen = {str(page["id"])}
    parent = page.get("parentId")
    while parent is not None and str(parent) in by_id and str(parent) not in seen:
        seen.add(str(parent))
        node = by_id[str(parent)]
        titles.append(node.get("title") or str(parent))
        parent = node.get("parentId")
    return list(reversed(titles))


def _render(
    title: str,
    source_url: str,
    key: str,
    ancestors: list[str],
    labels: list[str],
    updated: float | None,
    storage: str,
) -> str:
    return render_document(
        title,
        [
            ("Source", source_url),
            ("Space", key),
            ("Breadcrumb", " > ".join(ancestors)),
            ("Labels", ", ".join(lbl for lbl in labels if lbl)),
            ("Last updated", iso_date(updated)),
        ],
        storage_to_markdown(storage),
    )


# ---------------------------------------------------------------------------
# Storage format (XHTML + ac:/ri: elements) → Markdown
# ---------------------------------------------------------------------------


def storage_to_markdown(storage: str) -> str:
    """Convert Confluence storage format to Markdown.

    Code/noformat macros become fenced blocks, panels become quotes, macros
    that only carry layout or dynamic content (toc, children, attachments…)
    are dropped, user mentions are removed (personal data), page links keep
    their text.
    """
    if not storage.strip():
        return ""
    # Parsers disagree on CDATA outside SVG/MathML; plain escaped text is
    # read the same way everywhere.
    storage = _CDATA.sub(lambda m: html.escape(m.group(1), quote=False), storage)
    soup = BeautifulSoup(storage, "html.parser")

    # Innermost first, so an outer macro sees its children already converted.
    for macro in reversed(soup.find_all("ac:structured-macro")):
        _convert_macro(soup, macro)
    for link in soup.find_all("ac:link"):
        _convert_link(link)
    for task_list in reversed(soup.find_all("ac:task-list")):
        _convert_task_list(soup, task_list)
    for name in ("ac:image", "ac:emoticon", "ac:placeholder", "ac:parameter"):
        for tag in soup.find_all(name):
            tag.decompose()
    for tag in soup.find_all(re.compile(r"^(ac|ri):")):
        tag.unwrap()
    for tag in soup.find_all("time"):
        if not tag.get_text(strip=True) and tag.get("datetime"):
            tag.string = tag["datetime"]
    return html_to_markdown(soup)


def _params(macro: Tag) -> dict[str, str]:
    return {
        (p.get("ac:name") or "").lower(): p.get_text(strip=True)
        for p in macro.find_all("ac:parameter", recursive=False)
    }


def _convert_macro(soup: BeautifulSoup, macro: Tag) -> None:
    name = (macro.get("ac:name") or "").lower()
    params = _params(macro)
    rich = macro.find("ac:rich-text-body", recursive=False)
    plain = macro.find("ac:plain-text-body", recursive=False)

    if name in _CODE_MACROS or (plain is not None and rich is None):
        if plain is None:
            macro.decompose()
            return
        pre = soup.new_tag("pre")
        if params.get("language"):
            pre["data-language"] = params["language"]
        code = soup.new_tag("code")
        code.string = plain.get_text()
        pre.append(code)
        if params.get("title"):
            caption = soup.new_tag("p")
            caption.string = params["title"]
            macro.insert_before(caption)
        macro.replace_with(pre)
        return

    if name in ("status", "jira"):
        text = params.get("key", "") if name == "jira" else params.get("title", "")
        if not text:
            macro.decompose()
        else:
            macro.replace_with(f"[{text}]" if name == "status" else text)
        return

    if rich is None:
        # toc, children, attachments, anchor, pagetree, recently-updated…:
        # no text of their own.
        macro.decompose()
        return

    if name in _PANEL_MACROS:
        wrapper = soup.new_tag("blockquote")
        label = ": ".join(x for x in (_PANEL_MACROS[name], params.get("title", "")) if x)
        if label:
            head = soup.new_tag("p")
            strong = soup.new_tag("strong")
            strong.string = label
            head.append(strong)
            wrapper.append(head)
    else:
        wrapper = soup.new_tag("div")
        if params.get("title"):  # expand, details…
            head = soup.new_tag("p")
            strong = soup.new_tag("strong")
            strong.string = params["title"]
            head.append(strong)
            wrapper.append(head)
    for child in list(rich.contents):
        wrapper.append(child.extract())
    macro.replace_with(wrapper)


def _convert_link(link: Tag) -> None:
    if link.find("ri:user") is not None:
        # User mentions carry nothing but a person: people are not indexed.
        link.decompose()
        return
    body = link.find(["ac:link-body", "ac:plain-text-link-body"])
    text = body.get_text(" ", strip=True) if body is not None else ""
    if not text:
        target = link.find(["ri:page", "ri:blog-post", "ri:attachment", "ri:space"])
        if target is not None:
            text = (
                target.get("ri:content-title")
                or target.get("ri:filename")
                or target.get("ri:space-key")
                or ""
            )
    if text:
        link.replace_with(text)
    else:
        link.decompose()


def _convert_task_list(soup: BeautifulSoup, task_list: Tag) -> None:
    ul = soup.new_tag("ul")
    for task in task_list.find_all("ac:task", recursive=False):
        status = task.find("ac:task-status")
        done = status is not None and status.get_text(strip=True).lower() == "complete"
        li = soup.new_tag("li")
        li.append("[x] " if done else "[ ] ")
        body = task.find("ac:task-body")
        if body is not None:
            for child in list(body.contents):
                li.append(child.extract())
        ul.append(li)
    task_list.replace_with(ul)
