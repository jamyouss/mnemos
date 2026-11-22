from __future__ import annotations

import httpx
import pytest

from connectors.atlassian import auth_kwargs
from connectors.confluence import ConfluenceSource, file_path, scope_key, storage_to_markdown
from connectors.sync import sync_scope
from connectors.transport import parse_timestamp

from .conftest import NO_WAIT

CLOUD = "https://example.atlassian.net"
DC = "https://wiki.example.com/confluence"


# ---------------------------------------------------------------------------
# Fixtures shaped like real API answers
# ---------------------------------------------------------------------------


def _cloud_page(page_id: str, title: str, parent: str | None, updated: str) -> dict:
    return {
        "id": page_id,
        "status": "current",
        "title": title,
        "spaceId": "98306",
        "parentId": parent,
        "parentType": "page" if parent else None,
        "position": 0,
        "authorId": "557058:0f1e2d3c",
        "ownerId": "557058:0f1e2d3c",
        "createdAt": "2023-01-10T08:00:00.000Z",
        "version": {
            "number": 3,
            "message": "",
            "minorEdit": False,
            "authorId": "557058:0f1e2d3c",
            "createdAt": updated,
        },
        "_links": {
            "webui": f"/spaces/ENG/pages/{page_id}/{title.replace(' ', '+')}",
            "editui": f"/pages/resumedraft.action?draftId={page_id}",
            "tinyui": "/x/AQAB",
        },
    }


CLOUD_PAGES = {
    "65601": _cloud_page("65601", "Home", None, "2024-01-15T10:30:00.000Z"),
    "65602": _cloud_page("65602", "Runbooks", "65601", "2024-02-01T09:00:00.000Z"),
    "65603": _cloud_page("65603", "Restart the API", "65602", "2024-03-05T16:45:12.345Z"),
}

CLOUD_BODIES = {
    "65602": "<p>Index of runbooks.</p>",
    "65603": (
        "<h2>Steps</h2><ol><li><p>Drain traffic</p></li><li><p>Restart</p></li></ol>"
        '<ac:structured-macro ac:name="code" ac:schema-version="1">'
        '<ac:parameter ac:name="language">bash</ac:parameter>'
        "<ac:plain-text-body><![CDATA[kubectl rollout restart deploy/api && echo \"<done>\"]]></ac:plain-text-body>"
        "</ac:structured-macro>"
        '<p>Ping <ac:link><ri:user ri:account-id="557058:0f1e2d3c" /></ac:link> if stuck.</p>'
    ),
}


def _cloud_router(router, *, next_in_header: bool = False):
    router.add(
        f"{CLOUD}/wiki/api/v2/spaces",
        lambda r: httpx.Response(
            200,
            json={
                "results": [{"id": "98306", "key": r.url.params["keys"], "name": "Engineering", "type": "global"}],
                "_links": {"base": f"{CLOUD}/wiki"},
            },
        ),
    )

    def pages(request: httpx.Request) -> httpx.Response:
        if request.url.params.get("cursor") == "c2":
            return httpx.Response(200, json={"results": [CLOUD_PAGES["65603"]], "_links": {"base": f"{CLOUD}/wiki"}})
        next_url = "/wiki/api/v2/spaces/98306/pages?cursor=c2&limit=250"
        body = {"results": [CLOUD_PAGES["65601"], CLOUD_PAGES["65602"]], "_links": {"base": f"{CLOUD}/wiki"}}
        headers = {}
        if next_in_header:
            headers["Link"] = f'<{next_url}>; rel="next", <{CLOUD}/wiki>; rel="base"'
        else:
            body["_links"]["next"] = next_url
        return httpx.Response(200, json=body, headers=headers)

    router.add(f"{CLOUD}/wiki/api/v2/spaces/98306/pages", pages)
    for page_id, storage in CLOUD_BODIES.items():
        router.add(
            f"{CLOUD}/wiki/api/v2/pages/{page_id}",
            {
                **CLOUD_PAGES[page_id],
                "body": {"storage": {"value": storage, "representation": "storage"}},
                "labels": {
                    "results": [{"id": "1", "name": "runbook", "prefix": "global"}],
                    "meta": {"hasMore": False, "cursor": ""},
                    "_links": {},
                },
            },
        )


# ---------------------------------------------------------------------------
# Cloud (REST v2)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("next_in_header", [False, True])
def test_cloud_sync_paginates_and_fetches_only_changed_bodies(router, mnemos, next_in_header):
    _cloud_router(router, next_in_header=next_in_header)
    tags = ["confluence", "confluence:ENG"]
    mnemos.seed("confluence/ENG/65601.md", parse_timestamp("2024-01-15T10:30:00.000Z"), tags)
    mnemos.seed("confluence/ENG/99999.md", 1.0, tags)  # deleted at the source

    source = ConfluenceSource(router.client(), f"{CLOUD}/wiki/", retry=NO_WAIT)
    assert source.deployment == "cloud"
    result = sync_scope(mnemos.client(), source="confluence", scope="ENG", items=source.items("eng"))

    assert (result.pushed, result.unchanged, result.deleted, result.failed) == (2, 1, 1, 0)
    assert mnemos.deleted == ["confluence/ENG/99999.md"]

    listing = [r for r in router.requests if r.url.path.endswith("/pages")]
    assert listing[0].url.params["status"] == "current"
    assert listing[0].url.params["limit"] == "250"
    assert listing[1].url.params["cursor"] == "c2"
    # The unchanged page's body is never downloaded.
    assert f"/wiki/api/v2/pages/65601" not in router.paths()
    body_call = next(r for r in router.requests if r.url.path == "/wiki/api/v2/pages/65603")
    assert body_call.url.params["body-format"] == "storage"
    assert body_call.url.params["include-labels"] == "true"

    pushed = {p["file_path"]: p for p in mnemos.pushed}
    doc = pushed["confluence/ENG/65603.md"]
    assert doc["source_url"] == f"{CLOUD}/wiki/spaces/ENG/pages/65603/Restart+the+API"
    assert doc["file_mtime"] == parse_timestamp("2024-03-05T16:45:12.345Z")
    assert doc["tags"] == ["confluence", "confluence:ENG"]
    content = doc["content"]
    assert content.startswith("# Restart the API\n\n")
    assert f"- Source: {CLOUD}/wiki/spaces/ENG/pages/65603/Restart+the+API" in content
    assert "- Space: ENG" in content
    assert "- Breadcrumb: Home > Runbooks" in content
    assert "- Labels: runbook" in content
    assert "- Last updated: 2024-03-05 16:45 UTC" in content
    assert "## Steps" in content
    assert "1. Drain traffic" in content
    assert '```bash\nkubectl rollout restart deploy/api && echo "<done>"\n```' in content
    assert "557058" not in content  # the user mention is gone
    assert "Ping  if stuck." in content


def test_cloud_unknown_space_fails_without_deleting(router, mnemos):
    router.add(f"{CLOUD}/wiki/api/v2/spaces", {"results": [], "_links": {}})
    mnemos.seed("confluence/NOPE/1.md", 1.0, ["confluence", "confluence:NOPE"])
    source = ConfluenceSource(router.client(), CLOUD, retry=NO_WAIT)

    result = sync_scope(mnemos.client(), source="confluence", scope="NOPE", items=source.items("NOPE"))

    assert not result.ok and "not found" in result.error
    assert mnemos.deleted == []


# ---------------------------------------------------------------------------
# Data Center (REST v1)
# ---------------------------------------------------------------------------


def _dc_page(page_id: str, title: str, ancestors: list[tuple[str, str]], when: str) -> dict:
    return {
        "id": page_id,
        "type": "page",
        "status": "current",
        "title": title,
        "version": {
            "by": {"type": "known", "username": "jdoe", "userKey": "8a8b", "displayName": "Jane Doe"},
            "when": when,
            "number": 5,
            "minorEdit": False,
        },
        "ancestors": [{"id": a, "type": "page", "status": "current", "title": t} for a, t in ancestors],
        "metadata": {
            "labels": {"results": [{"prefix": "global", "name": "oncall", "id": "9"}], "start": 0, "limit": 200, "size": 1}
        },
        "_links": {
            "webui": f"/display/OPS/{title.replace(' ', '+')}",
            "tinyui": "/x/abc",
            "self": f"{DC}/rest/api/content/{page_id}",
        },
    }


def test_datacenter_sync_paginates_with_start_and_uses_bearer(router, mnemos):
    pages = [
        _dc_page("1001", "OPS Home", [], "2024-02-01T09:15:00.000+01:00"),
        _dc_page("1002", "Runbooks", [("1001", "OPS Home")], "2024-02-02T09:15:00.000+01:00"),
        _dc_page("1003", "On-call", [("1001", "OPS Home"), ("1002", "Runbooks")], "2024-02-03T09:15:00.000+01:00"),
    ]

    def listing(request: httpx.Request) -> httpx.Response:
        start = int(request.url.params["start"])
        links = {"base": DC, "context": "/confluence", "self": f"{DC}/rest/api/content"}
        if start == 0:
            links["next"] = "/rest/api/content?spaceKey=OPS&type=page&start=2&limit=2"
            return httpx.Response(200, json={"results": pages[:2], "start": 0, "limit": 2, "size": 2, "_links": links})
        return httpx.Response(200, json={"results": pages[2:], "start": 2, "limit": 2, "size": 1, "_links": links})

    router.add(f"{DC}/rest/api/content", listing)
    for page in pages:
        router.add(
            f"{DC}/rest/api/content/{page['id']}",
            {
                "id": page["id"],
                "title": page["title"],
                "body": {
                    "storage": {
                        "value": (
                            "<table><tbody><tr><th>Service</th><th>Port</th></tr>"
                            "<tr><td>api</td><td>8080</td></tr></tbody></table>"
                            '<ac:structured-macro ac:name="toc" />'
                            '<ac:structured-macro ac:name="warning"><ac:rich-text-body>'
                            "<p>Never restart both nodes.</p></ac:rich-text-body></ac:structured-macro>"
                        ),
                        "representation": "storage",
                    }
                },
            },
        )

    client = router.client(**auth_kwargs(None, "pat-secret"))
    source = ConfluenceSource(client, DC + "/", retry=NO_WAIT)
    assert source.deployment == "datacenter"
    result = sync_scope(mnemos.client(), source="confluence", scope="OPS", items=source.items("ops"))

    assert (result.pushed, result.failed) == (3, 0)
    assert all(r.headers["Authorization"] == "Bearer pat-secret" for r in router.requests)
    first, second = (r for r in router.requests if r.url.path == "/confluence/rest/api/content")
    assert first.url.params["spaceKey"] == "OPS"
    assert first.url.params["type"] == "page"
    assert first.url.params["status"] == "current"
    assert first.url.params["expand"] == "version,ancestors,metadata.labels"
    assert second.url.params["start"] == "2"
    body_call = next(r for r in router.requests if r.url.path == "/confluence/rest/api/content/1003")
    assert body_call.url.params["expand"] == "body.storage"

    doc = next(p for p in mnemos.pushed if p["file_path"] == "confluence/OPS/1003.md")
    assert doc["source_url"] == f"{DC}/display/OPS/On-call"
    assert doc["file_mtime"] == parse_timestamp("2024-02-03T08:15:00.000Z")
    content = doc["content"]
    assert "- Breadcrumb: OPS Home > Runbooks" in content
    assert "- Labels: oncall" in content
    assert "| Service | Port |\n| --- | --- |\n| api | 8080 |" in content
    assert "> **Warning**" in content and "> Never restart both nodes." in content
    assert "Jane Doe" not in content and "jdoe" not in content


# ---------------------------------------------------------------------------
# Storage format conversion
# ---------------------------------------------------------------------------


def test_storage_to_markdown_macros_links_and_tasks():
    storage = (
        '<p>See <ac:link><ri:page ri:content-title="Deploy guide" /></ac:link> and '
        '<ac:link><ri:page ri:content-title="X" /><ac:plain-text-link-body><![CDATA[the FAQ]]></ac:plain-text-link-body></ac:link>.</p>'
        '<ac:structured-macro ac:name="children" />'
        '<ac:structured-macro ac:name="info"><ac:parameter ac:name="title">Heads up</ac:parameter>'
        "<ac:rich-text-body><p>Freeze on Fridays.</p></ac:rich-text-body></ac:structured-macro>"
        '<ac:structured-macro ac:name="expand"><ac:parameter ac:name="title">Details</ac:parameter>'
        '<ac:rich-text-body><ac:structured-macro ac:name="noformat"><ac:plain-text-body><![CDATA[raw | text]]>'
        "</ac:plain-text-body></ac:structured-macro></ac:rich-text-body></ac:structured-macro>"
        '<p>State: <ac:structured-macro ac:name="status"><ac:parameter ac:name="title">IN PROGRESS</ac:parameter>'
        '</ac:structured-macro> ticket <ac:structured-macro ac:name="jira"><ac:parameter ac:name="key">OPS-12</ac:parameter>'
        "</ac:structured-macro></p>"
        "<ac:task-list><ac:task><ac:task-id>1</ac:task-id><ac:task-status>complete</ac:task-status>"
        "<ac:task-body>Backup</ac:task-body></ac:task><ac:task><ac:task-id>2</ac:task-id>"
        "<ac:task-status>incomplete</ac:task-status><ac:task-body>Restore test</ac:task-body></ac:task></ac:task-list>"
        '<ac:layout><ac:layout-section ac:type="two_equal"><ac:layout-cell><p>Left <ac:emoticon ac:name="smile" /></p>'
        '</ac:layout-cell><ac:layout-cell><ac:image><ri:attachment ri:filename="arch.png" /></ac:image></ac:layout-cell>'
        "</ac:layout-section></ac:layout>"
    )
    md = storage_to_markdown(storage)

    assert "See Deploy guide and the FAQ." in md
    assert "> **Info: Heads up**" in md and "> Freeze on Fridays." in md
    assert "**Details**" in md and "```\nraw | text\n```" in md
    assert "State: [IN PROGRESS] ticket OPS-12" in md
    assert "- [x] Backup" in md and "- [ ] Restore test" in md
    assert "Left" in md
    assert "arch.png" not in md and "smile" not in md
    assert "ac:" not in md and "ri:" not in md


def test_storage_to_markdown_empty():
    assert storage_to_markdown("   ") == ""


def test_scope_key_and_file_path():
    assert scope_key(" eng ") == "ENG"
    assert scope_key("~jdoe") == "~jdoe"
    assert file_path("ENG", "65601") == "confluence/ENG/65601.md"
