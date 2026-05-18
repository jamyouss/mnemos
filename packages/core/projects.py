"""Project detection — maps an indexed path to a list of tags.

Mnemos stores every code chunk with a `tags: list[str]` payload in the
`mnemos_code` (and `mnemos_memory`) Qdrant collection. The first tag is the
"primary" — used as the default display label — and the rest are
cross-cutting labels (parent projects, team, techno) that enable OR/AND
filtering at query time.

Two strategies, in order of precedence:

1. **YAML override** (`config/projects.yaml`, optional). Explicit
   path-prefix → list-of-tags mapping, longest-prefix-wins.
2. **Default convention**: cumulative path segments
   (`foo/bar/baz/file.go` → `["foo", "foo/bar", "foo/bar/baz"]`)
   so users with zero configuration still get a usable hierarchy.
"""
from __future__ import annotations

import logging
from pathlib import Path

logger = logging.getLogger("mnemos.projects")


PathTags = dict[str, list[str]]
"""Mapping path-prefix (relative to codebase root) → list of tags to apply
to every chunk whose file lives under that prefix. The first tag is the
'primary' (shown as the default display label in search results)."""


def detect_tags(
    rel_path: str,
    overrides: PathTags | None = None,
) -> list[str]:
    """Resolve the list of tags for a path relative to the codebase mount.

    Resolution order:
        1. Explicit ``overrides`` (longest matching prefix wins).
        2. Default convention: cumulative path-segments
           (``foo/bar/baz`` → ``["foo", "foo/bar", "foo/bar/baz"]``)
           so users with zero configuration still get a usable hierarchy.

    Returns an empty list for empty paths, absolute paths, or paths
    whose first segment is ``..`` (parent traversal blocked).
    """
    if not rel_path or rel_path.startswith("/"):
        return []

    # 1. Explicit overrides — longest matching prefix wins.
    if overrides:
        best_tags: list[str] | None = None
        best_len = 0
        for prefix, tags in overrides.items():
            if not prefix:
                continue
            normalised = prefix if prefix.endswith("/") else prefix + "/"
            if rel_path.startswith(normalised) and len(normalised) > best_len:
                best_tags = list(tags)
                best_len = len(normalised)
        if best_tags:
            return best_tags

    # 2. Default: cumulative segment hierarchy.
    parts = Path(rel_path).parts
    # Block parent traversal ('../foo' has parts == ('..', 'foo')).
    # pathlib normalises away leading './' so a bare '.' is the only way
    # the '.' guard ever fires (Path('.').parts == ('.',)).
    if not parts or parts[0] == "..":
        return []
    if parts[0] == ".":
        return [parts[0]]

    cumulative: list[str] = []
    acc = ""
    # Drop the filename: tags should reflect directories, not the file itself.
    dir_parts = parts[:-1] if len(parts) > 1 else parts
    for segment in dir_parts:
        if not segment:
            continue
        acc = f"{acc}/{segment}" if acc else segment
        cumulative.append(acc)
    return cumulative or [parts[0]]


def load_path_tags(config_path: Path | str) -> PathTags:
    """Load `config/projects.yaml` and return path → tags mapping.

    Expected YAML shape:

        paths:
          myproject/services/:
            - my-service
            - myproject
          shared/lib/:
            - lib-shared
            - shared

    Errors (missing PyYAML, parse failure, non-dict root, malformed entries)
    are logged and result in an empty dict — callers fall back to the default
    segment-based behaviour in that case.
    """
    path = Path(config_path)
    if not path.exists():
        return {}

    try:
        import yaml
    except ImportError:
        logger.warning("PyYAML not installed; cannot load %s", path)
        return {}

    try:
        with path.open("r", encoding="utf-8") as fh:
            raw = yaml.safe_load(fh) or {}
    except (OSError, yaml.YAMLError) as exc:
        logger.warning("Failed to read %s: %s", path, exc)
        return {}

    if not isinstance(raw, dict):
        return {}

    paths_block = raw.get("paths")
    if not isinstance(paths_block, dict):
        return {}

    out: PathTags = {}
    for prefix, value in paths_block.items():
        if not isinstance(value, list):
            logger.warning(
                "%s: entry for %r is not a list of tags; skipping.", path, prefix,
            )
            continue
        cleaned = [t.strip() for t in value if isinstance(t, str) and t.strip()]
        if cleaned:
            out[str(prefix)] = cleaned
    return out
