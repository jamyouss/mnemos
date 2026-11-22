from __future__ import annotations

from connectors.document import html_to_markdown, render_document


def test_render_document_shape():
    doc = render_document(
        "  Deploy   guide ",
        [("Source", "https://wiki.example.com/x"), ("Labels", ""), ("Space", "ENG")],
        "\n\nBody text\n\n\n\nMore\n",
    )
    assert doc == (
        "# Deploy guide\n\n"
        "- Source: https://wiki.example.com/x\n"
        "- Space: ENG\n\n"
        "Body text\n\nMore\n"
    )


def test_render_document_without_title_or_metadata():
    assert render_document("", [], "x") == "# Untitled\n\nx\n"


def test_tables_stay_tables_and_pipes_are_escaped():
    md = html_to_markdown(
        "<table><tr><th>Name</th><th>Value</th></tr>"
        "<tr><td><p>a|b</p><p>c</p></td><td>2</td></tr></table>"
    )
    assert "| Name | Value |" in md
    assert "| --- | --- |" in md
    assert r"| a\|b c | 2 |" in md


def test_code_language_and_images():
    md = html_to_markdown(
        '<pre><code class="language-go">x := 1</code></pre>'
        '<p>see <img src="a.png" alt="diagram"> and <img src="b.png"></p>'
    )
    assert "```go\nx := 1\n```" in md
    assert "[image: diagram]" in md
    assert "b.png" not in md


def test_snake_case_is_not_escaped():
    assert html_to_markdown("<p>call my_func_name</p>") == "call my_func_name"
