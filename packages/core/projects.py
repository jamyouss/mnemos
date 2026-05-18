"""Project detection — maps an indexed path to a project name.

Mnemos stores all code chunks in a single Qdrant collection (`mnemos_code`)
and uses a `project` payload field for per-project scoping. This module
decides which value to write into that payload.

Two strategies, in order of precedence:

1. **YAML override** (`config/projects.yaml`, optional). Explicit
   `path_prefixes` → `project name` mapping, longest-prefix-wins.
   Useful when your layout has org wrappers or monorepo splits.

2. **Default convention**: the first directory segment of the path is
   the project name (`myproject/services/handler.go` → `myproject`).

The default lets a user with `~/code/{a,b,c}/` mounted at `/data/codebase/`
get auto-detected projects with **zero configuration**. The YAML override is
the escape hatch for layouts that don't fit the convention.
"""
from __future__ import annotations

import logging
from pathlib import Path

logger = logging.getLogger("mnemos.projects")

ProjectOverrides = dict[str, list[str]]
"""Mapping of project name → list of path prefixes (relative to codebase root)
that should route to that project. Longest prefix wins on conflict."""


def detect_project(
    rel_path: str,
    overrides: ProjectOverrides | None = None,
) -> str | None:
    """Resolve a project name for a path relative to the codebase mount.

    Args:
        rel_path: Path relative to the codebase root (e.g. `myproject/handler.go`).
                  Leading slashes or empty strings yield None.
        overrides: Optional explicit mapping (typically from config/projects.yaml).

    Returns:
        Project name, or None if no segment could be derived.
    """
    if not rel_path or rel_path.startswith("/"):
        return None

    # 1. Explicit overrides — longest matching prefix wins.
    if overrides:
        best_project: str | None = None
        best_len = 0
        for project, prefixes in overrides.items():
            for prefix in prefixes:
                if not prefix:
                    continue
                # Normalise: prefix should end with "/" for path-aligned matching.
                normalised = prefix if prefix.endswith("/") else prefix + "/"
                if rel_path.startswith(normalised) and len(normalised) > best_len:
                    best_project = project
                    best_len = len(normalised)
        if best_project:
            return best_project

    # 2. Default convention: first directory segment.
    parts = Path(rel_path).parts
    if not parts:
        return None
    first = parts[0]
    if not first or first in (".", ".."):
        return None
    return first


def load_project_overrides(config_path: Path | str) -> ProjectOverrides:
    """Load `config/projects.yaml` if it exists; otherwise return an empty dict.

    Expected YAML shape:

        projects:
          myproject:
            path_prefixes: [myproject/]
          monorepo-app:
            path_prefixes:
              - apps/monorepo/
              - libs/shared/monorepo/

    Errors are logged and result in an empty overrides dict — the system
    falls back to the default first-segment convention.
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
        logger.warning("Failed to read project overrides from %s: %s", path, exc)
        return {}

    projects = raw.get("projects") or {}
    out: ProjectOverrides = {}
    for name, cfg in projects.items():
        if not isinstance(cfg, dict):
            logger.warning("Skipping malformed project entry %r in %s", name, path)
            continue
        prefixes = cfg.get("path_prefixes") or []
        if not isinstance(prefixes, list):
            logger.warning("Project %r has non-list path_prefixes; skipping", name)
            continue
        cleaned = [p for p in prefixes if isinstance(p, str) and p.strip()]
        if cleaned:
            out[str(name)] = cleaned
    return out


# ---------------------------------------------------------------------------
# Tags model (new) — replaces the single-value `project` over time.
# ---------------------------------------------------------------------------

PathTags = dict[str, list[str]]
"""Mapping path-prefix (relative to codebase root) → list of tags to apply
to every chunk whose file lives under that prefix. The first tag is the
'primary' (mirrored into the legacy `project` payload field for display)."""


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

    Two supported schemas:

    1. New (preferred) — keys are path-prefixes:

        paths:
          myproject/services/:
            - my-service
            - myproject

    2. Legacy — keys are project names with `path_prefixes:` lists:

        projects:
          my-service:
            path_prefixes: [myproject/services/]

    Legacy configs are converted to the new shape with a single-element
    tag list (`[project_name]`) and a one-time WARNING is logged. The
    function returns an empty dict on error rather than raising — callers
    fall back to the default first-segment behaviour in that case.
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

    # --- Path A: new schema ---
    if "paths" in raw and isinstance(raw["paths"], dict):
        return _parse_new_schema(raw["paths"], path)

    # --- Path B: legacy schema, auto-converted ---
    if "projects" in raw and isinstance(raw["projects"], dict):
        logger.warning(
            "%s: detected legacy `projects:` schema. It still works but the "
            "new `paths:` schema is recommended (see CONFIGURATION.md).",
            path,
        )
        return _convert_legacy_schema(raw["projects"])

    return {}


def _parse_new_schema(paths_block: dict, source: Path) -> PathTags:
    out: PathTags = {}
    for prefix, value in paths_block.items():
        if not isinstance(value, list):
            logger.warning(
                "%s: entry for %r is not a list of tags; skipping.", source, prefix,
            )
            continue
        cleaned = [t.strip() for t in value if isinstance(t, str) and t.strip()]
        if cleaned:
            out[str(prefix)] = cleaned
    return out


def _convert_legacy_schema(projects_block: dict) -> PathTags:
    out: PathTags = {}
    for project_name, cfg in projects_block.items():
        if not isinstance(cfg, dict):
            continue
        prefixes = cfg.get("path_prefixes") or []
        if not isinstance(prefixes, list):
            continue
        for prefix in prefixes:
            if isinstance(prefix, str) and prefix.strip():
                out[prefix] = [str(project_name)]
    return out
