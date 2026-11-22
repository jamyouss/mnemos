"""Markdown output shared by every connector.

Every pushed document has the same shape, so the server's Markdown heading
chunker treats them alike::

    # <title>

    - Source: <url>
    - <field>: <value>

    <body>
"""

from __future__ import annotations

import re
from collections.abc import Iterable

from bs4 import BeautifulSoup, Tag
from markdownify import MarkdownConverter

_BLANK_RUNS = re.compile(r"\n{3,}")


def render_document(title: str, metadata: Iterable[tuple[str, str]], body: str) -> str:
    """Assemble ``# title`` + a metadata list + the body, empty fields dropped."""
    lines = [f"# {_one_line(title) or 'Untitled'}", ""]
    meta = [f"- {label}: {_one_line(value)}" for label, value in metadata if value and value.strip()]
    if meta:
        lines.extend(meta)
        lines.append("")
    text = "\n".join(lines) + "\n" + body.strip() + "\n"
    return _BLANK_RUNS.sub("\n\n", text)


def _one_line(value: str) -> str:
    return " ".join(value.split())


def _cell(text: str) -> str:
    """One line per cell, pipes escaped: multi-paragraph cells must not break the row."""
    return _one_line(text).replace("|", r"\|")


class _Converter(MarkdownConverter):
    """markdownify tuned for knowledge pages: tables stay tables, images go."""

    def convert_td(self, el, text, parent_tags):  # noqa: D102 — markdownify hook
        return super().convert_td(el, _cell(text), parent_tags)

    def convert_th(self, el, text, parent_tags):  # noqa: D102
        return super().convert_th(el, _cell(text), parent_tags)

    def convert_img(self, el, text, parent_tags):  # noqa: D102
        # Image bytes are not indexed; the alt text is the only searchable part.
        alt = (el.get("alt") or "").strip()
        return f"[image: {alt}]" if alt else ""


def _code_language(el: Tag) -> str:
    language = el.get("data-language") or ""
    if not language:
        code = el.find("code")
        classes = code.get("class", []) if isinstance(code, Tag) else []
        for cls in classes:
            if cls.startswith("language-"):
                return cls[len("language-"):]
    return language


def html_to_markdown(html: str | BeautifulSoup | Tag) -> str:
    """Convert an HTML fragment (string or parsed soup) to Markdown."""
    converter = _Converter(
        heading_style="ATX",
        bullets="-",
        code_language_callback=_code_language,
        table_infer_header=True,
        escape_underscores=False,
        escape_asterisks=False,
    )
    if isinstance(html, str):
        html = BeautifulSoup(html, "html.parser")
    markdown = converter.convert_soup(html)
    return _BLANK_RUNS.sub("\n\n", markdown).strip()
