#!/usr/bin/env python3
"""Reindex everything: Claude skills/docs + every project declared in
`config/projects.yaml`.

Typical use:
    # First time after a schema migration (drops + recreates collections):
    ./scripts/reindex-all.py --recreate

    # Daily refresh, parallelised:
    ./scripts/reindex-all.py --workers 4

    # Just one slice:
    ./scripts/reindex-all.py --filter 'moby|trevio'

NOTE on async reindex:
    The server queues each reindex as a background task and returns
    `reindex_started` immediately. The final chunk count reported at the end
    of this script is a snapshot — for big repos the count keeps climbing
    after the script exits. Watch the server logs (or `mnemos status`) to
    know when everything has settled.

Prerequisites:
    * Mnemos server running (default http://localhost:8100, override via
      MNEMOS_URL).
    * Codebase mount inside the container at `/data/codebase` and Claude
      config at `/data/claude-config` (the docker-compose defaults).
    * Pyyaml installed (already a Mnemos dependency).
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from pathlib import Path
from urllib import error, request

REPO_ROOT = Path(__file__).resolve().parent.parent
PROJECTS_YAML = REPO_ROOT / "config" / "projects.yaml"
CODEBASE_PREFIX = "/data/codebase"
CLAUDE_CONFIG_PREFIX = "/data/claude-config"

ANSI_GREEN = "\033[32m"
ANSI_RED = "\033[31m"
ANSI_YELLOW = "\033[33m"
ANSI_DIM = "\033[2m"
ANSI_RESET = "\033[0m"


def log(msg: str, *, level: str = "info") -> None:
    prefix = {
        "info": "",
        "ok": f"{ANSI_GREEN}✓{ANSI_RESET} ",
        "warn": f"{ANSI_YELLOW}!{ANSI_RESET} ",
        "err": f"{ANSI_RED}✗{ANSI_RESET} ",
    }[level]
    print(f"{prefix}{msg}", flush=True)


def post_json(url: str, payload: dict, timeout: int = 60) -> dict:
    data = json.dumps(payload).encode("utf-8")
    req = request.Request(
        url,
        data=data,
        method="POST",
        headers={"content-type": "application/json"},
    )
    with request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def check_server(base_url: str) -> None:
    try:
        with request.urlopen(f"{base_url}/health", timeout=5) as resp:
            if resp.status != 200:
                raise RuntimeError(f"server returned {resp.status}")
    except (error.URLError, RuntimeError) as exc:
        log(f"Mnemos server unreachable at {base_url}: {exc}", level="err")
        log("  Start it first:  make up   (or: docker compose up -d)")
        sys.exit(2)


def load_paths(yaml_path: Path) -> dict[str, list[str]]:
    try:
        import yaml
    except ImportError:
        log("PyYAML not installed (pip install pyyaml).", level="err")
        sys.exit(2)
    if not yaml_path.exists():
        log(f"{yaml_path} not found — nothing to reindex on the code side.", level="warn")
        return {}
    with yaml_path.open("r", encoding="utf-8") as fh:
        raw = yaml.safe_load(fh) or {}
    paths = raw.get("paths") or {}
    out: dict[str, list[str]] = {}
    for prefix, tags in paths.items():
        if isinstance(tags, list) and tags:
            out[str(prefix).rstrip("/")] = [str(t).strip() for t in tags if str(t).strip()]
    return out


def reindex_one(
    base_url: str,
    *,
    collection: str,
    container_path: str,
    tags: list[str] | None,
    workers: int,
    recreate: bool,
) -> dict:
    payload = {
        "collection": collection,
        "path": container_path,
        "full": True,
        "recreate": recreate,
        "workers": workers,
    }
    if tags:
        payload["tags"] = tags
    return post_json(f"{base_url}/api/reindex", payload, timeout=120)


def wait_for_count_growth(base_url: str, collection: str, *, min_seconds: int = 2) -> int:
    """Poll /api/status briefly to surface the current chunk count."""
    deadline = time.monotonic() + min_seconds
    last_count = -1
    while time.monotonic() < deadline:
        try:
            with request.urlopen(f"{base_url}/api/status", timeout=5) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            last_count = (
                data.get("collections", {})
                    .get(collection, {})
                    .get("points_count", 0)
            )
        except (error.URLError, ValueError):
            pass
        time.sleep(0.5)
    return last_count


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument(
        "--mnemos-url",
        default=os.environ.get("MNEMOS_URL", "http://localhost:8100"),
        help="Mnemos server URL (default: env MNEMOS_URL or http://localhost:8100).",
    )
    parser.add_argument(
        "--workers", type=int, default=4,
        help="Parallel worker threads per reindex call (default: 4).",
    )
    parser.add_argument(
        "--recreate", action="store_true",
        help="Drop and recreate each collection before reindexing. Required when migrating "
             "from an old payload schema. ONLY the first call per collection is recreated.",
    )
    parser.add_argument(
        "--skip-skills", action="store_true",
        help="Don't reindex mnemos_skills.",
    )
    parser.add_argument(
        "--skip-docs", action="store_true",
        help="Don't reindex mnemos_docs.",
    )
    parser.add_argument(
        "--skip-code", action="store_true",
        help="Don't reindex mnemos_code (only run skills + docs).",
    )
    parser.add_argument(
        "--filter", default=None,
        help="Regex applied to YAML path prefixes — only matching ones are reindexed.",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Print what would run, but don't call the server.",
    )
    args = parser.parse_args()

    base_url = args.mnemos_url.rstrip("/")
    log(f"Target: {base_url}")

    if not args.dry_run:
        check_server(base_url)

    pattern = re.compile(args.filter) if args.filter else None

    # 1) Claude config
    claude_jobs: list[tuple[str, str]] = []
    if not args.skip_skills:
        claude_jobs.append(("mnemos_skills", f"{CLAUDE_CONFIG_PREFIX}/skills"))
    if not args.skip_docs:
        claude_jobs.append(("mnemos_docs", f"{CLAUDE_CONFIG_PREFIX}/docs"))

    # 2) Code paths from projects.yaml
    code_jobs: list[tuple[str, list[str]]] = []
    if not args.skip_code:
        paths = load_paths(PROJECTS_YAML)
        for rel, tags in paths.items():
            container_path = f"{CODEBASE_PREFIX}/{rel}"
            if pattern and not pattern.search(rel):
                continue
            code_jobs.append((container_path, tags))

    log(f"  Skills + docs:   {len(claude_jobs)} job(s)")
    log(f"  Code paths:      {len(code_jobs)} job(s) from {PROJECTS_YAML.name}")
    if args.recreate:
        log("  --recreate ON — first call per collection will drop the collection", level="warn")

    seen_recreate: set[str] = set()
    errors: list[tuple[str, str]] = []
    ok = 0

    def call(label: str, collection: str, container_path: str, tags: list[str] | None) -> None:
        nonlocal ok
        recreate = bool(args.recreate and collection not in seen_recreate)
        seen_recreate.add(collection)

        if args.dry_run:
            tag_part = f"  tags={tags}" if tags else ""
            log(f"  DRY-RUN  {label:35}  recreate={recreate}{tag_part}")
            ok += 1
            return

        t0 = time.monotonic()
        try:
            resp = reindex_one(
                base_url,
                collection=collection,
                container_path=container_path,
                tags=tags,
                workers=args.workers,
                recreate=recreate,
            )
        except (error.URLError, error.HTTPError, ValueError) as exc:
            elapsed = time.monotonic() - t0
            log(f"  {label:35}  failed after {elapsed:.1f}s: {exc}", level="err")
            errors.append((label, str(exc)))
            return

        elapsed = time.monotonic() - t0
        status = resp.get("status", "?")
        tag_part = f"  tags={','.join(tags)}" if tags else ""
        log(f"  {label:35}  {status} in {elapsed:.1f}s{tag_part}", level="ok")
        ok += 1

    # Run claude jobs
    if claude_jobs:
        print()
        log("--- Claude config ---")
        for collection, path in claude_jobs:
            call(collection, collection, path, tags=None)

    # Run code jobs
    if code_jobs:
        print()
        log(f"--- Code ({len(code_jobs)} project paths) ---")
        for container_path, tags in code_jobs:
            primary = tags[0] if tags else "?"
            call(primary, "mnemos_code", container_path, tags)

    # Wrap up
    print()
    log(f"{ok} job(s) succeeded, {len(errors)} failed")
    if errors:
        for label, err in errors[:10]:
            log(f"  {label}: {err}", level="err")
        if len(errors) > 10:
            log(f"  ... and {len(errors) - 10} more", level="err")
        return 1

    if not args.dry_run and not args.skip_code:
        print()
        count = wait_for_count_growth(base_url, "mnemos_code", min_seconds=3)
        log(f"mnemos_code current chunk count: {count}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
