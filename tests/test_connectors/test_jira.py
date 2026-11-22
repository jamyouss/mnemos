from __future__ import annotations

import base64

import httpx

from connectors.atlassian import auth_kwargs
from connectors.jira import JiraSource, adf_to_markdown, build_jql, file_path, wiki_to_markdown
from connectors.sync import sync_scope
from connectors.transport import parse_timestamp

from .conftest import NO_WAIT

CLOUD = "https://example.atlassian.net"
DC = "https://jira.example.com"

PEOPLE = ("Jane Doe", "jane@example.com", "John Roe", "john@example.com", "5b10ac8d82e05b22cc7d4ef5", "jdoe")


def _person(name: str, email: str) -> dict:
    return {
        "accountId": "5b10ac8d82e05b22cc7d4ef5",
        "name": "jdoe",
        "displayName": name,
        "emailAddress": email,
        "active": True,
    }


def _text(text: str, *marks: dict) -> dict:
    node: dict = {"type": "text", "text": text}
    if marks:
        node["marks"] = list(marks)
    return node


def _para(*content: dict) -> dict:
    return {"type": "paragraph", "content": list(content)}


ADF_DESCRIPTION = {
    "type": "doc",
    "version": 1,
    "content": [
        {"type": "heading", "attrs": {"level": 2}, "content": [_text("Symptoms")]},
        _para(
            _text("The "),
            _text("gateway", {"type": "strong"}),
            _text(" returns "),
            _text("502", {"type": "code"}),
            _text(" — see "),
            _text("dashboard", {"type": "link", "attrs": {"href": "https://grafana.example.com/d/api"}}),
            _text(". cc "),
            {"type": "mention", "attrs": {"id": "5b10ac8d82e05b22cc7d4ef5", "text": "@Jane Doe"}},
            {"type": "emoji", "attrs": {"shortName": ":fire:", "text": "🔥"}},
        ),
        {
            "type": "bulletList",
            "content": [
                {"type": "listItem", "content": [
                    _para(_text("only under load")),
                    {"type": "bulletList", "content": [
                        {"type": "listItem", "content": [_para(_text("> 500 rps"))]},
                    ]},
                ]},
                {"type": "listItem", "content": [_para(_text("since "), {"type": "date", "attrs": {"timestamp": "1704067200000"}})]},
            ],
        },
        {"type": "orderedList", "attrs": {"order": 1}, "content": [
            {"type": "listItem", "content": [_para(_text("scale up"))]},
            {"type": "listItem", "content": [_para(_text("retry"))]},
        ]},
        {"type": "codeBlock", "attrs": {"language": "java"}, "content": [_text("client.setTimeout(30);")]},
        {"type": "table", "attrs": {"isNumberColumnEnabled": False, "layout": "default"}, "content": [
            {"type": "tableRow", "content": [
                {"type": "tableHeader", "content": [_para(_text("Env"))]},
                {"type": "tableHeader", "content": [_para(_text("Rate"))]},
            ]},
            {"type": "tableRow", "content": [
                {"type": "tableCell", "content": [_para(_text("prod"))]},
                {"type": "tableCell", "content": [_para(_text("2|3%"))]},
            ]},
        ]},
        {"type": "panel", "attrs": {"panelType": "warning"}, "content": [_para(_text("Do not restart the DB."))]},
        {"type": "rule"},
        _para({"type": "inlineCard", "attrs": {"url": "https://wiki.example.com/x/AbC"}}, _text(" "),
              {"type": "status", "attrs": {"text": "BLOCKED", "color": "red"}}),
        {"type": "mediaSingle", "content": [{"type": "media", "attrs": {"id": "f00", "type": "file"}}]},
    ],
}


def _adf_comment(i: int, text: str) -> dict:
    return {
        "id": str(10000 + i),
        "author": _person("John Roe", "john@example.com"),
        "updateAuthor": _person("John Roe", "john@example.com"),
        "body": {"type": "doc", "version": 1, "content": [_para(_text(text))]},
        "created": f"2024-01-1{i}T09:00:00.000+0000",
        "updated": f"2024-01-1{i}T09:00:00.000+0000",
    }


def _cloud_issue(key: str, summary: str, updated: str, **extra_fields) -> dict:
    fields = {
        "summary": summary,
        "issuetype": {"id": "10004", "name": "Bug", "subtask": False},
        "status": {"id": "3", "name": "In Progress", "statusCategory": {"key": "indeterminate"}},
        "priority": {"id": "2", "name": "High"},
        "labels": ["api", "perf"],
        "components": [{"id": "10000", "name": "Gateway"}],
        "resolution": None,
        "created": "2024-01-10T08:00:00.000+0000",
        "updated": updated,
        "assignee": _person("Jane Doe", "jane@example.com"),
        "reporter": _person("Jane Doe", "jane@example.com"),
        "description": None,
        "comment": {"comments": [], "maxResults": 0, "total": 0, "startAt": 0},
    }
    fields.update(extra_fields)
    return {"expand": "", "id": key.split("-")[1], "self": f"{CLOUD}/rest/api/3/issue/{key}", "key": key, "fields": fields}


def test_cloud_search_jql_pagination_adf_and_no_people(router, mnemos):
    issues = [
        _cloud_issue(
            "OPS-1",
            "API returns 502 under load",
            "2024-01-15T10:30:00.000+0000",
            description=ADF_DESCRIPTION,
            comment={"comments": [_adf_comment(1, "Reproduced on staging.")], "maxResults": 1, "total": 2, "startAt": 0},
        ),
        _cloud_issue("OPS-2", "Unchanged issue", "2024-01-02T00:00:00.000+0000"),
        _cloud_issue("OPS-3", "Third", "2024-01-03T00:00:00.000+0000"),
    ]

    def search(request: httpx.Request) -> httpx.Response:
        if request.url.params.get("nextPageToken") == "tok2":
            return httpx.Response(200, json={"issues": issues[2:], "isLast": True})
        return httpx.Response(200, json={"issues": issues[:2], "nextPageToken": "tok2", "isLast": False})

    router.add(f"{CLOUD}/rest/api/3/search/jql", search)
    router.add(
        f"{CLOUD}/rest/api/3/issue/OPS-1/comment",
        {"comments": [_adf_comment(2, "Fixed by raising the timeout.")], "startAt": 1, "maxResults": 100, "total": 2},
    )
    tags = ["jira", "jira:OPS"]
    mnemos.seed("jira/OPS/OPS-2.md", parse_timestamp("2024-01-02T00:00:00.000+0000"), tags)
    mnemos.seed("jira/OPS/OPS-9.md", 1.0, tags)

    client = router.client(**auth_kwargs("bot@example.com", "api-token"))
    source = JiraSource(client, CLOUD, retry=NO_WAIT)
    result = sync_scope(mnemos.client(), source="jira", scope="OPS", items=source.items("ops", "status != Done"))

    assert (result.pushed, result.unchanged, result.deleted, result.failed) == (2, 1, 1, 0)
    searches = [r for r in router.requests if r.url.path == "/rest/api/3/search/jql"]
    assert len(searches) == 2
    assert searches[0].url.params["jql"] == 'project = "OPS" AND (status != Done) ORDER BY created ASC'
    assert "comment" in searches[0].url.params["fields"]
    assert "nextPageToken" not in searches[0].url.params
    assert searches[1].url.params["nextPageToken"] == "tok2"
    assert router.requests[0].headers["Authorization"] == "Basic " + base64.b64encode(b"bot@example.com:api-token").decode()
    extra = next(r for r in router.requests if r.url.path.endswith("/comment"))
    assert extra.url.params["startAt"] == "1"

    doc = next(p for p in mnemos.pushed if p["file_path"] == "jira/OPS/OPS-1.md")
    assert doc["source_url"] == f"{CLOUD}/browse/OPS-1"
    assert doc["file_mtime"] == parse_timestamp("2024-01-15T10:30:00.000+0000")
    md = doc["content"]
    assert md.startswith("# OPS-1: API returns 502 under load\n\n")
    for line in (
        f"- Source: {CLOUD}/browse/OPS-1",
        "- Type: Bug",
        "- Status: In Progress",
        "- Priority: High",
        "- Labels: api, perf",
        "- Components: Gateway",
        "- Created: 2024-01-10 08:00 UTC",
        "- Updated: 2024-01-15 10:30 UTC",
    ):
        assert line in md
    assert "Resolution" not in md  # unresolved: field omitted
    assert "## Description" in md
    assert "#### Symptoms" in md
    assert "The **gateway** returns `502` — see [dashboard](https://grafana.example.com/d/api). cc 🔥" in md
    assert "- only under load\n  - > 500 rps\n- since 2024-01-01" in md
    assert "1. scale up\n2. retry" in md
    assert "```java\nclient.setTimeout(30);\n```" in md
    assert "| Env | Rate |\n| --- | --- |\n| prod | 2\\|3% |" in md
    assert "> **Warning**" in md and "> Do not restart the DB." in md
    assert "<https://wiki.example.com/x/AbC> [BLOCKED]" in md
    assert "## Comments" in md
    assert "### Comment 1 (2024-01-11 09:00 UTC)\n\nReproduced on staging." in md
    assert "### Comment 2 (2024-01-12 09:00 UTC)\n\nFixed by raising the timeout." in md
    for person in PEOPLE:
        assert person not in md


def test_datacenter_search_startat_wiki_markup_and_bearer(router, mnemos):
    def dc_issue(key: str, description: str | None) -> dict:
        return {
            "id": key.split("-")[1],
            "key": key,
            "fields": {
                "summary": f"Summary {key}",
                "issuetype": {"name": "Task"},
                "status": {"name": "Done"},
                "priority": {"name": "Medium"},
                "labels": [],
                "components": [],
                "resolution": {"name": "Fixed"},
                "created": "2024-03-01T10:00:00.000+0100",
                "updated": "2024-03-02T10:00:00.000+0100",
                "assignee": _person("Jane Doe", "jane@example.com"),
                "description": description,
                "comment": {
                    "comments": [{
                        "id": "1",
                        "author": _person("John Roe", "john@example.com"),
                        "body": "Thanks [~jdoe], *verified* on {{prod}}.",
                        "created": "2024-03-02T11:00:00.000+0100",
                    }],
                    "maxResults": 1,
                    "total": 1,
                    "startAt": 0,
                },
            },
        }

    issues = [
        dc_issue("INFRA-1", "h2. Plan\n* step one\n** sub step\n# first\n{code:yaml}\nkey: value\n{code}\n||Host||Role||\n|db1|primary|"),
        dc_issue("INFRA-2", None),
        dc_issue("INFRA-3", "See [the runbook|https://wiki.example.com/x/1] and [https://status.example.com]."),
    ]

    def search(request: httpx.Request) -> httpx.Response:
        start = int(request.url.params["startAt"])
        return httpx.Response(200, json={"startAt": start, "maxResults": 2, "total": 3, "issues": issues[start:start + 2]})

    router.add(f"{DC}/rest/api/2/search", search)
    client = router.client(**auth_kwargs(None, "pat-jira"))
    source = JiraSource(client, DC, retry=NO_WAIT)
    assert source.deployment == "datacenter"
    result = sync_scope(mnemos.client(), source="jira", scope="INFRA", items=source.items("INFRA"))

    assert (result.pushed, result.failed) == (3, 0)
    searches = [r for r in router.requests if r.url.path == "/rest/api/2/search"]
    assert [r.url.params["startAt"] for r in searches] == ["0", "2"]
    assert searches[0].url.params["jql"] == 'project = "INFRA" ORDER BY created ASC'
    assert all(r.headers["Authorization"] == "Bearer pat-jira" for r in router.requests)

    md = next(p["content"] for p in mnemos.pushed if p["file_path"] == "jira/INFRA/INFRA-1.md")
    assert "- Resolution: Fixed" in md
    assert "- Updated: 2024-03-02 09:00 UTC" in md
    assert "#### Plan" in md
    assert "- step one\n  - sub step\n1. first" in md
    assert "```yaml\nkey: value\n```" in md
    assert "| Host | Role |\n| --- | --- |\n| db1 | primary |" in md
    assert "Thanks , **verified** on `prod`." in md
    for person in PEOPLE:
        assert person not in md

    md3 = next(p["content"] for p in mnemos.pushed if p["file_path"] == "jira/INFRA/INFRA-3.md")
    assert "See [the runbook](https://wiki.example.com/x/1) and <https://status.example.com>." in md3
    md2 = next(p["content"] for p in mnemos.pushed if p["file_path"] == "jira/INFRA/INFRA-2.md")
    assert "## Description" not in md2


def test_failed_search_page_blocks_deletes(router, mnemos):
    def search(request: httpx.Request) -> httpx.Response:
        if request.url.params.get("nextPageToken"):
            return httpx.Response(500, json={"errorMessages": ["boom"]})
        return httpx.Response(200, json={"issues": [_cloud_issue("OPS-1", "One", "2024-01-01T00:00:00.000+0000")], "nextPageToken": "t", "isLast": False})

    router.add(f"{CLOUD}/rest/api/3/search/jql", search)
    mnemos.seed("jira/OPS/OPS-7.md", 1.0, ["jira", "jira:OPS"])
    source = JiraSource(router.client(), CLOUD, retry=NO_WAIT)

    result = sync_scope(mnemos.client(), source="jira", scope="OPS", items=source.items("OPS"))

    assert result.pushed == 1
    assert mnemos.deleted == []
    assert not result.ok


def test_build_jql_and_file_path():
    assert build_jql("OPS") == 'project = "OPS" ORDER BY created ASC'
    assert build_jql("OPS", "  labels = runbook ") == 'project = "OPS" AND (labels = runbook) ORDER BY created ASC'
    assert file_path("OPS", "OPS-12") == "jira/OPS/OPS-12.md"


def test_wiki_markup_inline_and_blocks():
    md = wiki_to_markdown(
        "h1. Title\nbq. quoted\n{quote}\nmulti line\n{quote}\n{noformat}\nraw *text*\n{noformat}\n"
        "_italic_ and -struck- but not well-known or 2024-01-02\n!screenshot.png|thumbnail!\n----"
    )
    assert "### Title" in md
    assert "> quoted" in md and "> multi line" in md
    assert "```\nraw *text*\n```" in md
    assert "*italic* and ~~struck~~ but not well-known or 2024-01-02" in md
    assert "screenshot" not in md
    assert md.endswith("---")


def test_adf_handles_none_and_strings():
    assert adf_to_markdown(None) == ""
    assert adf_to_markdown("plain") == "plain"
    assert wiki_to_markdown(None) == ""
