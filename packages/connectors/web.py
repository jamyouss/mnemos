"""Web connector: a polite, bounded crawler for documentation sites.

From one or more seed URLs it follows links that stay on the same host and
under the seed's path prefix, honours ``robots.txt`` (``urllib.robotparser``,
including ``Crawl-delay``) and ``<meta name="robots">``, keeps only
``text/html`` answers and extracts the main content.

Each host is its own sync scope (tag ``web:<host>``). ``updated`` is the
``Last-Modified`` header when the server sends one; without it the page is
re-pushed on every run.
"""

from __future__ import annotations

import hashlib
import logging
import time
from collections import deque
from collections.abc import Callable, Iterable, Iterator
from urllib.parse import urljoin, urlsplit, urlunsplit
from urllib.robotparser import RobotFileParser

import httpx
from bs4 import BeautifulSoup

from connectors.document import html_to_markdown, render_document
from connectors.sync import ListingTruncated, SourceError, SourceItem
from connectors.transport import (
    DEFAULT_RETRY,
    USER_AGENT,
    RetryPolicy,
    iso_date,
    parse_http_date,
    request,
)

logger = logging.getLogger(__name__)

SOURCE = "web"
DEFAULT_MAX_PAGES = 200
MAX_CRAWL_DELAY = 30.0
ROBOTS_AGENT = "mnemos"

_DEFAULT_PORTS = {"http": 80, "https": 443}
_HTML_TYPES = ("text/html", "application/xhtml+xml")
# Links to these are never pages; skipping them saves a download each.
_SKIP_EXTENSIONS = (
    ".pdf", ".zip", ".gz", ".tgz", ".tar", ".7z", ".rar", ".exe", ".dmg", ".msi",
    ".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp", ".ico", ".bmp",
    ".mp3", ".mp4", ".webm", ".mov", ".avi",
    ".css", ".js", ".json", ".xml", ".rss", ".atom", ".txt", ".csv",
    ".woff", ".woff2", ".ttf", ".eot",
    ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx", ".odt",
)
_NOISE_TAGS = ("script", "style", "noscript", "template", "nav")
_CHROME_TAGS = ("header", "footer", "aside", "form")


def normalize_url(url: str) -> str | None:
    """Canonical form used for dedup and identity, or None if not http(s).

    Lower-cases scheme and host, drops the fragment and the default port,
    turns an empty path into ``/``. The query string is kept: it often
    selects the page.
    """
    try:
        parts = urlsplit(url.strip())
        port = parts.port
    except ValueError:
        return None
    scheme = parts.scheme.lower()
    if scheme not in _DEFAULT_PORTS or not parts.hostname:
        return None
    host = parts.hostname.lower()
    netloc = host if port in (None, _DEFAULT_PORTS[scheme]) else f"{host}:{port}"
    return urlunsplit((scheme, netloc, parts.path or "/", parts.query, ""))


def scope_of(url: str) -> str:
    """Sync scope of a normalized URL: its host (``host:port`` off the default port)."""
    return urlsplit(url).netloc


def scope_segment(scope: str) -> str:
    """Path segment for a scope.

    A single-label host (``build``, ``vendor``, ``coverage``…) could collide
    with a directory name the server refuses to index; the trailing dot
    (DNS absolute form of the same name) keeps it apart. Dotted hosts and
    ``host:port`` scopes are used as-is.
    """
    return scope if ("." in scope or ":" in scope) else f"{scope}."


def file_path(url: str) -> str:
    """``web/<host>/<sha1(normalized url)[:16]>.md``."""
    digest = hashlib.sha1(url.encode("utf-8")).hexdigest()[:16]
    return f"web/{scope_segment(scope_of(url))}/{digest}.md"


def path_prefix(seed: str) -> str:
    """Path prefix a seed confines the crawl to.

    ``/docs/`` → ``/docs/``; ``/docs`` → ``/docs/`` (plus ``/docs`` itself);
    ``/docs/index.html`` → ``/docs/``.
    """
    path = urlsplit(seed).path or "/"
    if path.endswith("/"):
        return path
    head, _, last = path.rpartition("/")
    return f"{head}/" if "." in last else f"{path}/"


def group_seeds(urls: Iterable[str]) -> dict[str, list[str]]:
    """Normalize seeds and group them by host scope, preserving order."""
    groups: dict[str, list[str]] = {}
    for raw in urls:
        url = normalize_url(raw)
        if url is None:
            raise ValueError(f"Not an http(s) URL: {raw!r}")
        seeds = groups.setdefault(scope_of(url), [])
        if url not in seeds:
            seeds.append(url)
    return groups


class WebSource:
    """Crawl one host from its seeds and yield each HTML page."""

    source = SOURCE

    def __init__(
        self,
        client: httpx.Client,
        *,
        max_pages: int = DEFAULT_MAX_PAGES,
        retry: RetryPolicy = DEFAULT_RETRY,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._client = client
        self._max_pages = max_pages
        self._retry = retry
        self._sleep = sleep

    def items(self, scope: str, seeds: list[str]) -> Iterator[SourceItem]:
        prefixes = [path_prefix(s) for s in seeds]
        robots = self._robots(seeds[0])
        delay = min(float(robots.crawl_delay(ROBOTS_AGENT) or 0), MAX_CRAWL_DELAY)

        def in_scope(url: str) -> bool:
            if scope_of(url) != scope:
                return False
            path = urlsplit(url).path or "/"
            return any(path.startswith(p) or path == p.rstrip("/") for p in prefixes)

        queue: deque[str] = deque(seeds)
        queued: set[str] = set(seeds)
        done: set[str] = set()
        errors: list[str] = []
        fetched = 0

        while queue:
            if fetched >= self._max_pages:
                raise ListingTruncated(f"--max-pages {self._max_pages} reached, {len(queue)} URL(s) left")
            url = queue.popleft()
            if not robots.can_fetch(ROBOTS_AGENT, url):
                logger.info("[web:%s] robots.txt disallows %s", scope, url)
                continue
            if fetched and delay:
                self._sleep(delay)
            try:
                response = request(self._client, "GET", url, retry=self._retry)
            except httpx.HTTPError as exc:
                errors.append(f"{url}: {exc}")
                continue
            fetched += 1

            if response.status_code in (404, 410):
                logger.info("[web:%s] %s is gone (%s)", scope, url, response.status_code)
                continue
            if response.status_code >= 400:
                errors.append(f"{url}: HTTP {response.status_code}")
                continue
            content_type = response.headers.get("content-type", "").lower()
            if not content_type.startswith(_HTML_TYPES):
                continue

            final = normalize_url(str(response.url)) or url
            if final != url and (not in_scope(final) or final in done):
                continue  # redirected out of scope, or onto a page already seen
            done.add(final)

            soup = BeautifulSoup(response.text, "html.parser")
            noindex, nofollow = _meta_robots(soup)
            if not nofollow:
                for link in _links(soup, final):
                    if link not in queued and in_scope(link):
                        queued.add(link)
                        queue.append(link)
            if noindex:
                continue

            updated = parse_http_date(response.headers.get("last-modified"))
            title = _title(soup) or final
            yield SourceItem(
                file_path=file_path(final),
                title=title,
                source_url=final,
                updated=updated,
                content=render_document(
                    title,
                    [("Source", final), ("Last modified", iso_date(updated))],
                    html_to_markdown(_main_content(soup)),
                ),
            )

        if errors:
            raise SourceError(f"{len(errors)} page(s) could not be fetched, first: {errors[0]}")

    def _robots(self, seed: str) -> RobotFileParser:
        parts = urlsplit(seed)
        robots_url = urlunsplit((parts.scheme, parts.netloc, "/robots.txt", "", ""))
        parser = RobotFileParser(robots_url)
        try:
            response = request(self._client, "GET", robots_url, retry=self._retry)
        except httpx.HTTPError as exc:
            raise SourceError(f"cannot read {robots_url}: {exc}") from exc
        status = response.status_code
        if status in (401, 403):
            # robotparser reads this as "disallow everything"; refusing the run
            # instead keeps a transient WAF denial from wiping the host's pages.
            raise SourceError(f"{robots_url} answered HTTP {status}")
        if status >= 500:
            raise SourceError(f"{robots_url} answered HTTP {status}")
        if status >= 400:
            parser.parse([])  # no robots.txt: everything is allowed
            parser.allow_all = True
        else:
            parser.parse(response.text.splitlines())
        return parser


def _meta_robots(soup: BeautifulSoup) -> tuple[bool, bool]:
    noindex = nofollow = False
    for meta in soup.find_all("meta", attrs={"name": True}):
        if meta["name"].lower() not in ("robots", ROBOTS_AGENT):
            continue
        directives = {d.strip().lower() for d in (meta.get("content") or "").split(",")}
        noindex |= bool(directives & {"noindex", "none"})
        nofollow |= bool(directives & {"nofollow", "none"})
    return noindex, nofollow


def _links(soup: BeautifulSoup, base: str) -> Iterator[str]:
    for anchor in soup.find_all("a", href=True):
        href = anchor["href"].strip()
        if not href or href.startswith(("#", "mailto:", "javascript:", "tel:")):
            continue
        rel = anchor.get("rel") or []
        if "nofollow" in [r.lower() for r in rel]:
            continue
        url = normalize_url(urljoin(base, href))
        if url and not urlsplit(url).path.lower().endswith(_SKIP_EXTENSIONS):
            yield url


def _title(soup: BeautifulSoup) -> str:
    for tag in (soup.title, soup.find("h1")):
        if tag is not None:
            text = " ".join(tag.get_text(" ", strip=True).split())
            if text:
                return text
    return ""


def _main_content(soup: BeautifulSoup):
    for tag in soup.find_all(_NOISE_TAGS):
        tag.decompose()
    main = soup.find("main") or soup.find("article") or soup.find(attrs={"role": "main"})
    if main is not None:
        return main
    body = soup.body or soup
    for tag in body.find_all(_CHROME_TAGS):
        tag.decompose()
    return body
