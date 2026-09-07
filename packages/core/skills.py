"""Skill metadata extraction for the `mnemos_skills` collection.

`SkillResult` carries a `skill_name` and a `description`, and the
`mnemos_search_skills` tool exists so an agent can find a skill it cannot name
("is there something for auditing Vue accessibility?"). Both fields were read
at query time but never written at index time, so the tool returned the right
content under an empty name — it found the skill and could not say which one.

Two layouts are in the wild:

    skills/<slug>/SKILL.md          YAML frontmatter with name + description
    skills/<slug>/metadata.yaml     same keys, no frontmatter fence
    skills/<slug>/instructions.md   prose, no metadata of its own

The slug always comes from the path, so every chunk of a skill is attributable
even when it lives in `references/` or `templates/`. The description only
comes from a file that carries one; nothing here reads a sibling file, because
`core` does no I/O.
"""
from __future__ import annotations

import re
from pathlib import PurePosixPath

# Cap what we keep: a description is a one-liner for a search result, and some
# skills write a paragraph.
_MAX_DESCRIPTION = 500

_FRONTMATTER = re.compile(r"\A---\s*\n(.*?)\n---\s*(?:\n|\Z)", re.DOTALL)
# `key: value`, value running to end of line. Enough for these two flat keys;
# a real YAML parse would also pull in multi-line scalars we do not want.
_KEY = r"^{key}\s*:\s*(?P<value>.+?)\s*$"


def detect_skill_name(file_path: str) -> str:
    """Slug of the skill a path belongs to, or "" when it is not under one.

    `skills/docs-craft/templates/readme.md` → `docs-craft`, so a chunk from a
    template is still attributed to its skill.
    """
    if not file_path:
        return ""
    parts = PurePosixPath(file_path).parts
    try:
        index = len(parts) - 1 - parts[::-1].index("skills")
    except ValueError:
        return ""
    if index + 1 >= len(parts):
        return ""
    candidate = parts[index + 1]
    # `skills/SKILL.md` has no slug — the next part is the file itself.
    return "" if "." in candidate else candidate


def extract_skill_description(content: str, file_path: str = "") -> str:
    """Description declared by this file, or "" when it declares none.

    Reads YAML frontmatter (`SKILL.md`) or a bare `description:` key
    (`metadata.yaml`). A file that carries neither — `instructions.md`, a
    template, a script — yields nothing rather than a guess.
    """
    if not content:
        return ""

    match = _FRONTMATTER.match(content)
    block = match.group(1) if match else None

    if block is None:
        # metadata.yaml has the same keys without the fence. Restrict this to
        # .yaml/.yml so a Markdown body mentioning "description:" mid-prose is
        # not mistaken for metadata.
        if not file_path.endswith((".yaml", ".yml")):
            return ""
        block = content

    found = re.search(_KEY.format(key="description"), block, re.MULTILINE)
    if not found:
        return ""
    value = found.group("value").strip().strip("\"'")
    return value[:_MAX_DESCRIPTION]


def skill_payload(content: str, file_path: str) -> dict[str, str]:
    """Payload fields to attach to a chunk of the skills collection.

    Empty values are dropped so a chunk never carries a field that says
    nothing — an empty `skill_name` in a result is what made this tool look
    broken in the first place.
    """
    out = {
        "skill_name": detect_skill_name(file_path),
        "description": extract_skill_description(content, file_path),
    }
    return {k: v for k, v in out.items() if v}
