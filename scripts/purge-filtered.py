#!/usr/bin/env python3
"""Drop already-indexed chunks that the current ignore policy would reject.

The ignore policy only gates *new* indexing. When a rule is added — a
built-in in `core.path_filter`, or per-prefix `exclude_dirs` / `exclude_exts`
in `config/projects.yaml` — every chunk ingested before it stays in Qdrant and
keeps competing in search results. `reindex-all.py --recreate` would clear
them, but it re-embeds the entire corpus to do it. This walks the collection
and deletes just the points the current policy would now reject.

It calls `core.indexer.resolve_skip`, the same function the ingest path uses,
so the purge can never disagree with what indexing would do.

Usage:
    ./scripts/purge-filtered.py --dry-run       # report only (default)
    ./scripts/purge-filtered.py --apply         # actually delete

Take a snapshot first:
    curl -X POST http://localhost:6333/collections/mnemos_code/snapshots
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from pathlib import Path
from urllib import error, request

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "packages"))

from core.indexer import resolve_skip  # noqa: E402
from core.projects import load_path_excludes  # noqa: E402

DEFAULT_QDRANT = "http://localhost:6333"
DEFAULT_PROJECTS_CONFIG = Path(__file__).resolve().parent.parent / "config" / "projects.yaml"
DEFAULT_CODEBASE_ROOT = "/data/codebase"
SCROLL_PAGE = 4000
DELETE_BATCH = 1000


def post(url: str, body: dict, timeout: int = 120) -> dict:
    req = request.Request(
        url,
        data=json.dumps(body).encode("utf-8"),
        method="POST",
        headers={"content-type": "application/json"},
    )
    with request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def scroll_all(qdrant: str, collection: str) -> list[tuple[str, str]]:
    """Return [(point_id, file_path)] for the whole collection."""
    out: list[tuple[str, str]] = []
    offset = None
    while True:
        body: dict = {
            "limit": SCROLL_PAGE,
            "with_payload": ["file_path"],
            "with_vector": False,
        }
        if offset is not None:
            body["offset"] = offset
        result = post(f"{qdrant}/collections/{collection}/points/scroll", body)["result"]
        for point in result["points"]:
            out.append((point["id"], (point.get("payload") or {}).get("file_path", "")))
        offset = result.get("next_page_offset")
        if offset is None:
            return out


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--qdrant", default=DEFAULT_QDRANT)
    parser.add_argument("--collection", default="mnemos_code")
    parser.add_argument(
        "--projects-config", default=str(DEFAULT_PROJECTS_CONFIG),
        help="projects.yaml carrying the per-prefix exclude rules.",
    )
    parser.add_argument(
        "--codebase-root", default=DEFAULT_CODEBASE_ROOT,
        help="Container mount root that indexed paths are relative to.",
    )
    parser.add_argument("--apply", action="store_true", help="Delete. Without it, report only.")
    parser.add_argument("--dry-run", action="store_true", help="Explicit no-op (the default).")
    args = parser.parse_args()

    try:
        rows = scroll_all(args.qdrant, args.collection)
    except (error.URLError, error.HTTPError) as exc:
        print(f"Qdrant unreachable at {args.qdrant}: {exc}", file=sys.stderr)
        return 2

    excludes = load_path_excludes(args.projects_config)
    doomed = [
        (pid, fp) for pid, fp in rows
        if fp and resolve_skip(fp, args.codebase_root, excludes)
    ]
    orphans = [pid for pid, fp in rows if not fp]

    print(f"collection      : {args.collection}")
    print(f"règles projet   : {len(excludes)} préfixe(s) depuis {args.projects_config}")
    print(f"chunks totaux   : {len(rows)}")
    print(f"à supprimer     : {len(doomed)}  ({100 * len(doomed) / max(len(rows), 1):.1f}%)")
    if orphans:
        print(f"sans file_path  : {len(orphans)} (laissés en place)")

    if doomed:
        print("\nrépartition:")
        buckets = Counter(
            re.sub(r"^(/data/codebase(?:/[^/]+){0,5}).*", r"\1", fp) for _, fp in doomed
        )
        for path, n in buckets.most_common(10):
            print(f"  {n:6}  {path}")

    if not args.apply:
        print("\n(dry-run — relancer avec --apply pour supprimer)")
        return 0

    if not doomed:
        print("\nrien à supprimer.")
        return 0

    ids = [pid for pid, _ in doomed]
    deleted = 0
    for i in range(0, len(ids), DELETE_BATCH):
        batch = ids[i:i + DELETE_BATCH]
        post(
            f"{args.qdrant}/collections/{args.collection}/points/delete?wait=true",
            {"points": batch},
        )
        deleted += len(batch)
        print(f"  supprimé {deleted}/{len(ids)}", flush=True)

    remaining = len(scroll_all(args.qdrant, args.collection))
    print(f"\nchunks restants : {remaining}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
