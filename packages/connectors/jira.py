"""Jira connector — Cloud (REST v3 enhanced search) and Data Center (REST v2).

Endpoints:

* **Cloud** — ``GET /rest/api/3/search/jql`` paginated by ``nextPageToken``
  (the old ``/rest/api/{2,3}/search`` is being removed, CHANGE-2046). Rich
  text fields come back as Atlassian Document Format (ADF) JSON.
  Extra comments: ``GET /rest/api/3/issue/{key}/comment``.
* **Data Center** — ``GET /rest/api/2/search`` paginated by ``startAt`` /
  ``total``. Rich text fields are wiki markup.
  Extra comments: ``GET /rest/api/2/issue/{key}/comment``.

People are never written out: assignee, reporter, comment authors and
@mentions are left out on purpose (personal data).
"""

from __future__ import annotations

import logging
import re
from collections.abc import Iterator
from datetime import datetime, timezone
from functools import partial

import httpx

from connectors.atlassian import detect_deployment, site_root
from connectors.document import render_document
from connectors.sync import SourceItem
from connectors.transport import DEFAULT_RETRY, RetryPolicy, get_json, iso_date, parse_timestamp

logger = logging.getLogger(__name__)

SOURCE = "jira"
PAGE_SIZE = 100
FIELDS = (
    "summary,issuetype,status,priority,labels,components,resolution,"
    "created,updated,description,comment"
)


def scope_key(project: str) -> str:
    """Jira project keys are upper-case; normalise what the user typed."""
    return project.strip().upper()


def file_path(project_key: str, issue_key: str) -> str:
    return f"jira/{project_key}/{issue_key}.md"


def build_jql(project_key: str, extra: str | None = None) -> str:
    """``project = KEY`` AND the optional user clause, oldest first.

    Ordering by creation keeps ``startAt`` pagination stable while new
    issues are being created during the run.
    """
    jql = f'project = "{project_key}"'
    if extra and extra.strip():
        jql += f" AND ({extra.strip()})"
    return jql + " ORDER BY created ASC"


class JiraSource:
    """List the issues of one project as :class:`SourceItem` objects."""

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
        self._base = site_root(url)
        self.deployment = detect_deployment(url, deployment)
        self._api = f"{self._base}/rest/api/{'3' if self.deployment == 'cloud' else '2'}"

    def items(self, project: str, jql: str | None = None) -> Iterator[SourceItem]:
        key = scope_key(project)
        query = build_jql(key, jql)
        issues = self._cloud_search(query) if self.deployment == "cloud" else self._dc_search(query)
        for issue in issues:
            yield self._item(issue, key)

    def _cloud_search(self, jql: str) -> Iterator[dict]:
        token: str | None = None
        while True:
            params: dict = {"jql": jql, "fields": FIELDS, "maxResults": PAGE_SIZE}
            if token:
                params["nextPageToken"] = token
            data = get_json(self._client, f"{self._api}/search/jql", params=params, retry=self._retry)
            yield from data.get("issues") or []
            token = data.get("nextPageToken")
            if data.get("isLast", not token) or not token:
                return

    def _dc_search(self, jql: str) -> Iterator[dict]:
        start = 0
        while True:
            data = get_json(
                self._client,
                f"{self._api}/search",
                params={"jql": jql, "fields": FIELDS, "startAt": start, "maxResults": PAGE_SIZE},
                retry=self._retry,
            )
            issues = data.get("issues") or []
            yield from issues
            start += len(issues)
            if not issues or start >= int(data.get("total") or 0):
                return

    def _item(self, issue: dict, project_key: str) -> SourceItem:
        key = issue["key"]
        fields = issue.get("fields") or {}
        summary = fields.get("summary") or ""
        return SourceItem(
            file_path=file_path(project_key, key),
            title=f"{key}: {summary}" if summary else key,
            source_url=f"{self._base}/browse/{key}",
            updated=parse_timestamp(fields.get("updated")),
            content=partial(self._render, issue),
        )

    def _render(self, issue: dict) -> str:
        key = issue["key"]
        fields = issue.get("fields") or {}
        summary = fields.get("summary") or ""
        to_md = adf_to_markdown if self.deployment == "cloud" else wiki_to_markdown

        sections: list[str] = []
        description = to_md(fields.get("description")).strip()
        if description:
            sections.append(f"## Description\n\n{description}")
        comments = self._comments(key, fields.get("comment") or {})
        rendered = []
        for i, comment in enumerate(comments, 1):
            body = to_md(comment.get("body")).strip()
            if not body:
                continue
            created = iso_date(parse_timestamp(comment.get("created")))
            heading = f"### Comment {i}" + (f" ({created})" if created else "")
            rendered.append(f"{heading}\n\n{body}")
        if rendered:
            sections.append("## Comments\n\n" + "\n\n".join(rendered))

        return render_document(
            f"{key}: {summary}" if summary else key,
            [
                ("Source", f"{self._base}/browse/{key}"),
                ("Type", _name(fields.get("issuetype"))),
                ("Status", _name(fields.get("status"))),
                ("Priority", _name(fields.get("priority"))),
                ("Resolution", _name(fields.get("resolution"))),
                ("Labels", ", ".join(fields.get("labels") or [])),
                ("Components", ", ".join(_name(c) for c in fields.get("components") or [])),
                ("Created", iso_date(parse_timestamp(fields.get("created")))),
                ("Updated", iso_date(parse_timestamp(fields.get("updated")))),
            ],
            "\n\n".join(sections),
        )

    def _comments(self, key: str, page: dict) -> list[dict]:
        """Comments embedded in the search hit, completed when it was cut short."""
        comments = list(page.get("comments") or [])
        total = int(page.get("total") or len(comments))
        while len(comments) < total:
            data = get_json(
                self._client,
                f"{self._api}/issue/{key}/comment",
                params={"startAt": len(comments), "maxResults": PAGE_SIZE},
                retry=self._retry,
            )
            batch = data.get("comments") or []
            if not batch:
                break
            comments.extend(batch)
            total = int(data.get("total") or total)
        return comments


def _name(value: object) -> str:
    return (value.get("name") or "") if isinstance(value, dict) else ""


# ---------------------------------------------------------------------------
# Atlassian Document Format (Cloud) → Markdown
# ---------------------------------------------------------------------------


def adf_to_markdown(node: dict | str | None) -> str:
    """Render an ADF document as Markdown. Strings pass through (v2 payloads)."""
    if node is None:
        return ""
    if isinstance(node, str):
        return node
    return _block(node, indent="").strip()


def _children(node: dict) -> list[dict]:
    return [c for c in node.get("content") or [] if isinstance(c, dict)]


def _inline(nodes: list[dict]) -> str:
    return "".join(_inline_node(n) for n in nodes)


def _inline_node(node: dict) -> str:
    kind = node.get("type")
    attrs = node.get("attrs") or {}
    if kind == "text":
        return _apply_marks(node.get("text", ""), node.get("marks") or [])
    if kind == "hardBreak":
        return "\n"
    if kind == "mention":
        return ""  # people are not indexed
    if kind == "emoji":
        return attrs.get("text") or attrs.get("shortName") or ""
    if kind in ("inlineCard", "blockCard", "embedCard"):
        url = attrs.get("url") or ""
        return f"<{url}>" if url else ""
    if kind == "status":
        return f"[{attrs.get('text', '')}]"
    if kind == "date":
        try:
            stamp = int(attrs.get("timestamp")) / 1000
        except (TypeError, ValueError):
            return ""
        return datetime.fromtimestamp(stamp, timezone.utc).strftime("%Y-%m-%d")
    if kind in ("media", "mediaInline", "mediaSingle", "mediaGroup"):
        return ""
    return _inline(_children(node))


def _apply_marks(text: str, marks: list[dict]) -> str:
    link = None
    for mark in marks:
        kind = mark.get("type")
        if kind == "code":
            text = f"`{text}`"
        elif kind == "strong":
            text = f"**{text}**"
        elif kind == "em":
            text = f"*{text}*"
        elif kind == "strike":
            text = f"~~{text}~~"
        elif kind == "link":
            link = (mark.get("attrs") or {}).get("href")
    return f"[{text}]({link})" if link else text


def _block(node: dict, indent: str) -> str:
    kind = node.get("type")
    attrs = node.get("attrs") or {}
    children = _children(node)

    if kind == "doc":
        return "\n\n".join(b for b in (_block(c, indent) for c in children) if b.strip())
    if kind == "paragraph":
        return _inline(children)
    if kind == "heading":
        level = min(max(int(attrs.get("level") or 1), 1), 6) + 2  # under the doc's ## sections
        return "#" * min(level, 6) + " " + _inline(children)
    if kind == "codeBlock":
        return f"```{attrs.get('language') or ''}\n{_inline(children)}\n```"
    if kind in ("bulletList", "orderedList", "taskList", "decisionList"):
        return _list(node, indent)
    if kind == "blockquote":
        inner = "\n\n".join(_block(c, "") for c in children)
        return "\n".join(f"> {line}" if line else ">" for line in inner.splitlines())
    if kind == "panel":
        inner = "\n\n".join(_block(c, "") for c in children)
        label = (attrs.get("panelType") or "").capitalize()
        lines = ([f"**{label}**", ""] if label else []) + inner.splitlines()
        return "\n".join(f"> {line}" if line else ">" for line in lines)
    if kind in ("expand", "nestedExpand"):
        title = attrs.get("title") or ""
        inner = "\n\n".join(_block(c, indent) for c in children)
        return (f"**{title}**\n\n" if title else "") + inner
    if kind == "rule":
        return "---"
    if kind == "table":
        return _table(node)
    if kind in ("mediaSingle", "mediaGroup", "media"):
        return ""
    if kind in ("blockCard", "embedCard"):
        return _inline_node(node)
    # Unknown block (layoutSection, bodiedExtension…): keep whatever text it has.
    if children:
        return "\n\n".join(b for b in (_block(c, indent) for c in children) if b.strip())
    return _inline_node(node)


def _list(node: dict, indent: str) -> str:
    kind = node.get("type")
    lines: list[str] = []
    for i, item in enumerate(_children(node), 1):
        if kind == "orderedList":
            marker = f"{i}."
        elif kind == "taskList":
            done = (item.get("attrs") or {}).get("state") == "DONE"
            marker = "- [x]" if done else "- [ ]"
        else:
            marker = "-"
        parts: list[str] = []
        nested: list[str] = []
        for child in _children(item):
            if child.get("type") in ("bulletList", "orderedList", "taskList"):
                nested.append(_list(child, indent + "  "))
            elif child.get("type") in ("text", "mention", "emoji", "hardBreak", "inlineCard", "status", "date"):
                parts.append(_inline_node(child))  # taskItem/decisionItem hold inline nodes
            else:
                parts.append(_block(child, indent + "  "))
        text = " ".join(p.replace("\n", " ") for p in parts if p).strip()
        lines.append(f"{indent}{marker} {text}".rstrip())
        lines.extend(nested)
    return "\n".join(lines)


def _table(node: dict) -> str:
    rows: list[list[str]] = []
    header = False
    for i, row in enumerate(_children(node)):
        cells = _children(row)
        if i == 0:
            header = all(c.get("type") == "tableHeader" for c in cells)
        rows.append(
            [
                " ".join(_block(c, "") for c in _children(cell)).replace("\n", " ").replace("|", r"\|").strip()
                for cell in cells
            ]
        )
    if not rows:
        return ""
    width = max(len(r) for r in rows)
    rows = [r + [""] * (width - len(r)) for r in rows]
    if not header:
        rows.insert(0, [""] * width)
    out = ["| " + " | ".join(rows[0]) + " |", "| " + " | ".join(["---"] * width) + " |"]
    out.extend("| " + " | ".join(r) + " |" for r in rows[1:])
    return "\n".join(out)


# ---------------------------------------------------------------------------
# Jira wiki markup (Data Center) → Markdown
# ---------------------------------------------------------------------------

_HEADING = re.compile(r"^h([1-6])\.\s+(.*)$")
_LIST = re.compile(r"^([*#-]+)\s+(.*)$")
_BLOCK_OPEN = re.compile(r"^\{(code|noformat)(?::([^}]*))?\}(.*)$")
_QUOTE_TOGGLE = re.compile(r"^\{quote\}(.*)$")
_PANEL = re.compile(r"\{(panel|color|div|section|column)(?::[^}]*)?\}")

_INLINE_RULES: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"\[~[^\]]+\]"), ""),  # user mentions: people are not indexed
    (re.compile(r"\{\{(.+?)\}\}"), r"`\1`"),
    (re.compile(r"\[([^\]|]+)\|([^\]]+)\]"), r"[\1](\2)"),
    (re.compile(r"\[((?:https?|mailto):[^\]]+)\]"), r"<\1>"),
    (re.compile(r"(?<![\w*])\*(?=\S)([^*\n]+?)(?<=\S)\*(?![\w*])"), r"**\1**"),
    (re.compile(r"(?<![\w_])_(?=\S)([^_\n]+?)(?<=\S)_(?![\w_])"), r"*\1*"),
    (re.compile(r"(?<![\w-])-(?=\S)([^-\n]+?)(?<=\S)-(?![\w-])"), r"~~\1~~"),
    (re.compile(r"![^!\s][^!]*!"), ""),  # embedded images / attachments
)


def wiki_to_markdown(text: str | dict | None) -> str:
    """Best-effort Jira wiki markup → Markdown, enough to read and search.

    Covers headings, lists, tables, code/noformat blocks, quotes, links and
    the usual inline styles. ADF dicts (a v3 payload) are delegated.
    """
    if text is None:
        return ""
    if isinstance(text, dict):
        return adf_to_markdown(text)

    out: list[str] = []
    in_code = False
    in_quote = False
    table_header_done = False
    for raw in text.replace("\r\n", "\n").split("\n"):
        line = raw.rstrip()
        if in_code:
            if re.match(r"^\{(code|noformat)\}", line.strip()):
                out.append("```")
                in_code = False
            else:
                out.append(raw)
            continue

        stripped = line.strip()
        block = _BLOCK_OPEN.match(stripped)
        if block:
            options = block.group(2) or ""
            language = "" if block.group(1) == "noformat" else options.split("|")[0].strip()
            if "=" in language:
                language = ""
            out.append(f"```{language}")
            rest = block.group(3)
            # Single-line block: {code}x = 1{code}
            closing = re.search(r"\{(code|noformat)\}$", rest)
            if closing:
                out.append(rest[: closing.start()])
                out.append("```")
            else:
                if rest:
                    out.append(rest)
                in_code = True
            continue

        quote = _QUOTE_TOGGLE.match(stripped)
        if quote:
            in_quote = not in_quote
            if quote.group(1):
                out.append("> " + _wiki_inline(quote.group(1).replace("{quote}", "")))
            continue

        stripped = _PANEL.sub("", stripped).replace("{panel}", "").replace("{color}", "").strip()
        md = _wiki_line(stripped)
        if stripped.startswith("||"):
            table_header_done = True
        elif stripped.startswith("|"):
            if not table_header_done:
                width = stripped.count("|") - 1
                out.append("| " + " | ".join([""] * max(width, 1)) + " |")
                out.append("| " + " | ".join(["---"] * max(width, 1)) + " |")
                table_header_done = True
        else:
            table_header_done = False
        out.append(("> " + md) if in_quote and md else md)

    if in_code:
        out.append("```")
    return re.sub(r"\n{3,}", "\n\n", "\n".join(out)).strip()


def _wiki_line(line: str) -> str:
    if not line:
        return ""
    heading = _HEADING.match(line)
    if heading:
        # Issue documents use ## sections; wiki headings nest below them.
        level = min(int(heading.group(1)) + 2, 6)
        return "#" * level + " " + _wiki_inline(heading.group(2))
    if line.startswith("bq. "):
        return "> " + _wiki_inline(line[4:])
    if line.startswith("||"):
        cells = [c.strip() for c in line.strip("|").split("||")]
        row = "| " + " | ".join(_wiki_inline(c) for c in cells) + " |"
        return row + "\n| " + " | ".join(["---"] * len(cells)) + " |"
    if line.startswith("|"):
        cells = [c.strip() for c in line.strip("|").split("|")]
        return "| " + " | ".join(_wiki_inline(c) for c in cells) + " |"
    if line == "----":
        return "---"
    listed = _LIST.match(line)
    if listed and not (listed.group(1).startswith("-") and len(listed.group(1)) > 1):
        markers = listed.group(1)
        depth = len(markers) - 1
        bullet = "1." if markers[-1] == "#" else "-"
        return "  " * depth + f"{bullet} " + _wiki_inline(listed.group(2))
    return _wiki_inline(line)


def _wiki_inline(text: str) -> str:
    for pattern, replacement in _INLINE_RULES:
        text = pattern.sub(replacement, text)
    return text.strip()
