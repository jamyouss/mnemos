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
