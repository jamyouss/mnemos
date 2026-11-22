from __future__ import annotations

import hashlib

import httpx
import pytest

from connectors.sync import sync_scope
from connectors.web import (
    WebSource,
    file_path,
    group_seeds,
    normalize_url,
    path_prefix,
    scope_segment,
)

from .conftest import NO_WAIT

SITE = "https://docs.example.com"


def _html(title: str, body: str, *, head: str = "") -> httpx.Response:
    page = (
        f"<html><head><title>{title}</title>{head}</head><body>"
        '<header><a href="/">Logo</a></header>'
        '<nav><a href="/guide/nav-only">Nav</a></nav>'
        f"{body}"
        "<footer>Copyright Example Corp</footer>"
        "</body></html>"
    )
    return httpx.Response(200, text=page, headers={"Content-Type": "text/html; charset=utf-8"})


def _site(router, robots: str | httpx.Response = "User-agent: *\nDisallow: /guide/private/\n") -> None:
    if isinstance(robots, str):
        robots = httpx.Response(200, text=robots, headers={"Content-Type": "text/plain"})
    router.add(f"{SITE}/robots.txt", robots)
    router.add(
        f"{SITE}/guide/",
        lambda r: _html(
            "Guide",
            "<main><h1>Guide</h1><p>Welcome to the guide.</p>"
            '<a href="install#step-2">Install</a> <a href="/guide/install">Install again</a>'
            '<a href="/guide/private/secrets">Secrets</a>'
            '<a href="/blog/post">Blog (outside prefix)</a>'
            '<a href="https://other.example.org/guide/x">Other host</a>'
            '<a href="/guide/manual.pdf">PDF</a> <a href="/guide/data">Data</a>'
            '<a href="mailto:docs@example.com">mail</a>'
            "<script>var x = 1;</script></main>",
        ),
    )
    router.add(
        f"{SITE}/guide/install",
        lambda r: httpx.Response(
            200,
            text="<html><head><title>Install</title></head><body><article><h2>Steps</h2>"
            "<pre><code class=\"language-bash\">pip install example</code></pre></article></body></html>",
            headers={"Content-Type": "text/html", "Last-Modified": "Mon, 15 Jan 2024 10:30:00 GMT"},
        ),
    )
    router.add(
        f"{SITE}/guide/data",
        httpx.Response(200, json={"not": "html"}),
    )
    router.add(f"{SITE}/guide/nav-only", _html("Nav only", "<p>Reached through the nav.</p>"))


def test_crawl_respects_robots_host_prefix_and_content_type(router, mnemos):
    _site(router)
    mnemos.seed("web/docs.example.com/0000000000000000.md", 1.0, ["web", "web:docs.example.com"])
    source = WebSource(router.client(), retry=NO_WAIT)

    result = sync_scope(
        mnemos.client(), source="web", scope="docs.example.com", items=source.items("docs.example.com", [f"{SITE}/guide/"])
    )

    fetched = router.paths()
    assert fetched[0] == "/robots.txt"
    assert "/guide/private/secrets" not in fetched  # robots.txt
    assert "/blog/post" not in fetched  # outside the seed prefix
    assert not any(r.url.host == "other.example.org" for r in router.requests)  # other host
    assert "/guide/manual.pdf" not in fetched  # binary extension
    assert fetched.count("/guide/install") == 1  # fragment dropped, deduped

    assert (result.pushed, result.failed, result.deleted) == (3, 0, 1)  # guide, install, nav-only; not the JSON
    by_url = {p["source_url"]: p for p in mnemos.pushed}
    assert set(by_url) == {f"{SITE}/guide/", f"{SITE}/guide/install", f"{SITE}/guide/nav-only"}

    guide = by_url[f"{SITE}/guide/"]
    assert guide["file_path"] == file_path(f"{SITE}/guide/")
    assert guide["tags"] == ["web", "web:docs.example.com"]
    assert "file_mtime" not in guide  # no Last-Modified: always re-pushed
    assert guide["content"].startswith(f"# Guide\n\n- Source: {SITE}/guide/\n")
    assert "Welcome to the guide." in guide["content"]
    for noise in ("Logo", "Copyright", "var x"):
        assert noise not in guide["content"]

    install = by_url[f"{SITE}/guide/install"]
    assert install["file_mtime"] == 1705314600.0
    assert "- Last modified: 2024-01-15 10:30 UTC" in install["content"]
    assert "```bash\npip install example\n```" in install["content"]

    nav = by_url[f"{SITE}/guide/nav-only"]["content"]
    assert "Reached through the nav." in nav and "Copyright" not in nav


def test_unchanged_page_with_last_modified_is_skipped(router, mnemos):
    _site(router)
    mnemos.seed(file_path(f"{SITE}/guide/install"), 1705314600.0, ["web", "web:docs.example.com"])
    source = WebSource(router.client(), retry=NO_WAIT)
    result = sync_scope(mnemos.client(), source="web", scope="docs.example.com", items=source.items("docs.example.com", [f"{SITE}/guide/"]))
    assert result.unchanged == 1
    assert file_path(f"{SITE}/guide/install") not in mnemos.pushed_paths


def test_robots_disallow_all_yields_nothing_but_404_robots_allows(router, mnemos):
    _site(router, robots="User-agent: mnemos\nDisallow: /\n")
    source = WebSource(router.client(), retry=NO_WAIT)
    assert list(source.items("docs.example.com", [f"{SITE}/guide/"])) == []

    router.routes.clear()
    router.requests.clear()
    _site(router, robots=httpx.Response(404))
    assert len(list(source.items("docs.example.com", [f"{SITE}/guide/"]))) == 3


@pytest.mark.parametrize("status", [403, 503])
def test_unreadable_robots_fails_the_listing_without_deletes(router, mnemos, status):
    _site(router, robots=httpx.Response(status))
    mnemos.seed("web/docs.example.com/aaaa.md", 1.0, ["web", "web:docs.example.com"])
    source = WebSource(router.client(), retry=NO_WAIT)

    result = sync_scope(mnemos.client(), source="web", scope="docs.example.com", items=source.items("docs.example.com", [f"{SITE}/guide/"]))

    assert not result.ok and "robots.txt" in result.error
    assert mnemos.deleted == [] and mnemos.pushed == []
    assert router.paths() == ["/robots.txt"] * (3 if status == 503 else 1)


def test_max_pages_truncates_and_never_deletes(router, mnemos):
    _site(router)
    mnemos.seed("web/docs.example.com/aaaa.md", 1.0, ["web", "web:docs.example.com"])
    source = WebSource(router.client(), max_pages=1, retry=NO_WAIT)

    result = sync_scope(mnemos.client(), source="web", scope="docs.example.com", items=source.items("docs.example.com", [f"{SITE}/guide/"]))

    assert result.pushed == 1 and result.truncated and result.ok
    assert mnemos.deleted == []


def test_page_errors_are_reported_and_block_deletes(router, mnemos):
    _site(router)
    router.add(f"{SITE}/guide/install", httpx.Response(500))
    mnemos.seed("web/docs.example.com/aaaa.md", 1.0, ["web", "web:docs.example.com"])
    source = WebSource(router.client(), retry=NO_WAIT)

    result = sync_scope(mnemos.client(), source="web", scope="docs.example.com", items=source.items("docs.example.com", [f"{SITE}/guide/"]))

    assert result.pushed == 2  # the other pages still land
    assert not result.ok and "could not be fetched" in result.error
    assert mnemos.deleted == []


def test_meta_robots_noindex_and_redirect_out_of_scope(router):
    router.add(f"{SITE}/robots.txt", httpx.Response(404))
    router.add(
        f"{SITE}/kb/",
        lambda r: _html(
            "KB",
            '<main><a href="/kb/hidden">hidden</a><a href="/kb/moved">moved</a></main>',
        ),
    )
    router.add(
        f"{SITE}/kb/hidden",
        lambda r: _html("Hidden", '<main><a href="/kb/child">child</a></main>', head='<meta name="robots" content="noindex">'),
    )
    router.add(f"{SITE}/kb/child", _html("Child", "<main>child text</main>"))
    router.add(f"{SITE}/kb/moved", httpx.Response(301, headers={"Location": f"{SITE}/elsewhere/"}))
    router.add(f"{SITE}/elsewhere/", _html("Elsewhere", "<main>out of scope</main>"))

    items = list(WebSource(router.client(), retry=NO_WAIT).items("docs.example.com", [f"{SITE}/kb/"]))

    assert [i.source_url for i in items] == [f"{SITE}/kb/", f"{SITE}/kb/child"]  # hidden: noindex but followed


def test_crawl_delay_is_honoured(router):
    _site(router, robots="User-agent: *\nCrawl-delay: 2\n")
    waits: list[float] = []
    items = list(WebSource(router.client(), retry=NO_WAIT, sleep=waits.append).items("docs.example.com", [f"{SITE}/guide/"]))
    assert len(items) == 3
    assert waits and set(waits) == {2.0}


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("HTTPS://Docs.Example.COM:443/guide/#intro", "https://docs.example.com/guide/"),
        ("http://docs.example.com", "http://docs.example.com/"),
        ("http://docs.example.com:8080/a?b=1#c", "http://docs.example.com:8080/a?b=1"),
        ("ftp://docs.example.com/file", None),
        ("mailto:someone@example.com", None),
    ],
)
def test_normalize_url(raw, expected):
    assert normalize_url(raw) == expected


def test_path_prefix():
    assert path_prefix("https://docs.example.com/") == "/"
    assert path_prefix("https://docs.example.com/guide/") == "/guide/"
    assert path_prefix("https://docs.example.com/guide") == "/guide/"
    assert path_prefix("https://docs.example.com/guide/index.html") == "/guide/"


def test_group_seeds_by_host():
    groups = group_seeds(
        ["https://docs.example.com/a/", "https://docs.example.com/b/#x", "https://docs.example.com/a/", "http://wiki.example.org/"]
    )
    assert groups == {
        "docs.example.com": ["https://docs.example.com/a/", "https://docs.example.com/b/"],
        "wiki.example.org": ["http://wiki.example.org/"],
    }
    with pytest.raises(ValueError):
        group_seeds(["not a url"])


def test_file_path_and_scope_segment():
    url = "https://docs.example.com/guide/"
    digest = hashlib.sha1(url.encode()).hexdigest()[:16]
    assert file_path(url) == f"web/docs.example.com/{digest}.md"
    assert file_path("http://build/x").startswith("web/build./")
    assert scope_segment("intranet:8080") == "intranet:8080"
