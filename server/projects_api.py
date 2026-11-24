"""REST surface of the dashboard's Projects page: /api/projects.

The `projects` config document maps codebase path prefixes to tags and
per-prefix ignore rules (see `core.projects`). Saving it re-tags what is
indexed next on every pod within one poll; chunks already indexed keep their
tags until their file is indexed again.
"""
from __future__ import annotations

from typing import Any

from core.config_store import ConfigDoc, RevisionConflict
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from server.audit import audit, keyed_changes
from server.config import settings
from server.config_api import require_admin_writer, revision_conflict
from server.principal import principal_of, require_admin_or_write_all

projects_router = APIRouter()

PROJECTS_KEY = "projects"


class ProjectsPutRequest(BaseModel):
    paths: list[Any]
    revision: int | None = None


def _str_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [v for v in value if isinstance(v, str)]


def _rows(data: dict) -> list[dict[str, Any]]:
    """The stored mapping as the page's rows, whichever entry form each uses."""
    paths = data.get("paths") if isinstance(data.get("paths"), dict) else {}
    rows = []
    for prefix, value in paths.items():
        entry = value if isinstance(value, dict) else {"tags": value}
        rows.append({
            "prefix": prefix,
            "tags": _str_list(entry.get("tags")),
            "exclude_dirs": _str_list(entry.get("exclude_dirs")),
            "exclude_exts": _str_list(entry.get("exclude_exts")),
        })
    return rows


def _validate(rows: list[Any]) -> tuple[list[dict[str, Any]], list[str]]:
    """Normalised rows and every problem, as `paths[<i>]: <message>`."""
    clean: list[dict[str, Any]] = []
    problems: list[str] = []
    seen: dict[str, list[int]] = {}
    for index, row in enumerate(rows):
        where = f"paths[{index}]"
        if not isinstance(row, dict):
            problems.append(f"{where}: expected an object")
            continue
        prefix = row.get("prefix")
        prefix = prefix.strip() if isinstance(prefix, str) else ""
        if not prefix:
            problems.append(f"{where}: empty prefix")
        elif prefix.startswith("/"):
            problems.append(f"{where}: prefix must be relative to the codebase root (no leading /)")
        else:
            seen.setdefault(prefix.rstrip("/") + "/", []).append(index)
        lists: dict[str, list[str]] = {}
        for field in ("tags", "exclude_dirs", "exclude_exts"):
            value = row.get(field)
            if value is None:
                value = []
            if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
                problems.append(f"{where}: {field} must be a list of strings")
                value = None
            lists[field] = [v.strip() for v in value or [] if v.strip()]
            if field == "tags" and value is not None and not lists["tags"]:
                problems.append(f"{where}: no tags")
        clean.append({"prefix": prefix, **lists})
    for prefix, indexes in seen.items():
        if len(indexes) > 1:
            problems.extend(f"paths[{i}]: duplicate prefix {prefix!r}" for i in indexes)
    return clean, problems


def _data(rows: list[dict[str, Any]]) -> dict:
    """Rows back to the stored form: the list shorthand unless a row has ignore rules."""
    paths: dict[str, Any] = {}
    for row in rows:
        if row["exclude_dirs"] or row["exclude_exts"]:
            entry: dict[str, Any] = {"tags": row["tags"]}
            if row["exclude_dirs"]:
                entry["exclude_dirs"] = row["exclude_dirs"]
            if row["exclude_exts"]:
                entry["exclude_exts"] = row["exclude_exts"]
            paths[row["prefix"]] = entry
        else:
            paths[row["prefix"]] = row["tags"]
    return {"paths": paths}


def _snapshot(doc: ConfigDoc | None) -> dict:
    rows = _rows(doc.data) if doc else []
    return {
        "revision": doc.revision if doc else 0,
        "writable": settings.mnemos_ui_config_writable,
        "paths": rows,
        "problems": _validate(rows)[1],
    }


@projects_router.get("/api/projects")
def get_projects(request: Request):
    require_admin_or_write_all(principal_of(request))
    return _snapshot(request.app.state.config_docs.get(PROJECTS_KEY))


@projects_router.put("/api/projects")
def put_projects(body: ProjectsPutRequest, request: Request):
    require_admin_writer(request)
    rows, problems = _validate(body.paths)
    if problems:
        return JSONResponse(
            status_code=422,
            content={"detail": "Invalid projects, nothing was saved", "problems": problems},
        )
    stored = request.app.state.config_docs.get(PROJECTS_KEY)
    try:
        doc = request.app.state.config_docs.put(
            PROJECTS_KEY, _data(rows), expected_revision=body.revision,
        )
    except RevisionConflict as exc:
        return revision_conflict(exc)
    request.app.state.config_sync.apply(doc)
    by_prefix = lambda rs: {r["prefix"]: {k: v for k, v in r.items() if k != "prefix"} for r in rs}  # noqa: E731
    audit(request, "projects.update", "projects",
          keyed_changes(by_prefix(_rows(stored.data) if stored else []), by_prefix(rows)))
    return _snapshot(doc)
