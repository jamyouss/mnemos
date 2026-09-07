"""Tests for skill metadata extraction.

`mnemos_search_skills` exists so an agent can find a skill it cannot name.
Returning the right content under an empty `skill_name` defeats the whole
point, so these pin that every chunk of a skill is attributable.
"""
from __future__ import annotations

import pytest

from core.skills import detect_skill_name, extract_skill_description, skill_payload

SKILL_MD = """---
name: docs-craft
description: Use when writing or overhauling a README or technical guide.
---

# Docs Craft

Produce documentation that is beautiful.
"""

METADATA_YAML = """name: knowledge-synthesizer
description: Knowledge synthesis specialist combining multiple sources.
model: sonnet
tools:
  - Read
"""


# ---------------------------------------------------------------------------
# Slug from the path
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "path,expected",
    [
        ("/data/claude-config/skills/docs-craft/SKILL.md", "docs-craft"),
        ("/data/claude-config/skills/docs-craft/metadata.yaml", "docs-craft"),
        # A chunk from deep inside a skill still belongs to that skill — that
        # is the case an empty skill_name used to lose.
        ("/data/claude-config/skills/docs-craft/templates/readme.md", "docs-craft"),
        ("/data/claude-config/skills/impeccable/scripts/lib/staleness.mjs", "impeccable"),
        ("skills/animate/SKILL.md", "animate"),
    ],
)
def test_slug_comes_from_the_path(path: str, expected: str) -> None:
    assert detect_skill_name(path) == expected


@pytest.mark.parametrize(
    "path",
    [
        "/data/codebase/Projects/acme/src/main.go",
        "/data/claude-config/docs/DDD_PATTERNS.md",
        # `skills/` immediately followed by a file has no slug to take.
        "/data/claude-config/skills/README.md",
        "",
    ],
)
def test_paths_outside_a_skill_yield_nothing(path: str) -> None:
    assert detect_skill_name(path) == ""


def test_a_nested_skills_directory_uses_the_innermost_one():
    """`skills` can appear twice; the one that names the slug is the last."""
    path = "/data/claude-config/skills/meta/skills/nested-one/SKILL.md"
    assert detect_skill_name(path) == "nested-one"


# ---------------------------------------------------------------------------
# Description, only where one is declared
# ---------------------------------------------------------------------------


def test_frontmatter_description_is_read():
    got = extract_skill_description(SKILL_MD, "skills/docs-craft/SKILL.md")
    assert got == "Use when writing or overhauling a README or technical guide."


def test_bare_yaml_description_is_read():
    got = extract_skill_description(METADATA_YAML, "skills/x/metadata.yaml")
    assert got == "Knowledge synthesis specialist combining multiple sources."


def test_prose_without_metadata_yields_nothing():
    """instructions.md, templates and scripts declare no description. Guessing
    one from the body would put arbitrary prose in a metadata field."""
    body = "# Knowledge Synthesizer\n\nYou are an expert synthesizer.\n"
    assert extract_skill_description(body, "skills/x/instructions.md") == ""


def test_markdown_prose_mentioning_description_is_not_metadata():
    """A guide *about* writing descriptions must not have its own sentence
    lifted into the payload."""
    body = "# Guide\n\nEvery skill needs one:\n\ndescription: keep it short\n"
    assert extract_skill_description(body, "skills/x/references/guide.md") == ""


def test_quotes_are_stripped():
    md = '---\nname: x\ndescription: "Quoted description."\n---\n'
    assert extract_skill_description(md, "skills/x/SKILL.md") == "Quoted description."


def test_a_long_description_is_capped():
    md = f"---\nname: x\ndescription: {'word ' * 300}\n---\n"
    assert len(extract_skill_description(md, "skills/x/SKILL.md")) <= 500


def test_empty_content_yields_nothing():
    assert extract_skill_description("", "skills/x/SKILL.md") == ""


# ---------------------------------------------------------------------------
# The payload the indexer attaches
# ---------------------------------------------------------------------------


def test_payload_carries_both_when_both_exist():
    assert skill_payload(SKILL_MD, "skills/docs-craft/SKILL.md") == {
        "skill_name": "docs-craft",
        "description": "Use when writing or overhauling a README or technical guide.",
    }


def test_payload_keeps_the_name_when_the_file_has_no_description():
    """A template still belongs to its skill, and that is the field the search
    result needs most."""
    assert skill_payload("# Template\n", "skills/docs-craft/templates/readme.md") == {
        "skill_name": "docs-craft",
    }


def test_payload_is_empty_outside_the_skills_tree():
    """Empty fields are dropped rather than written blank — a blank
    skill_name in a result is exactly what made the tool look broken."""
    assert skill_payload("package main\n", "/data/codebase/Projects/x/main.go") == {}
