"""Move every collection to a new embedding model without losing data or uptime.

Run it where the server runs (same Qdrant, same codebase mount):

    python -m core.embedding_migration sync   --model google/embeddinggemma-2
    python -m core.embedding_migration switch --model google/embeddinggemma-2

`sync` copies each collection into `<name>__<model-slug>_<dim>`, re-embedding
the dense vector from the stored `content` and keeping sparse vectors and
payloads as they are, so memories and connector pages, which have no source
file, survive too. It is repeatable: a second run only re-embeds what changed
since the first, which keeps the final catch-up short.

`switch` turns each logical name into an alias of its copy. The first switch
snapshots and deletes the original collection, since an alias cannot share a
collection's name. Later switches only move the alias, atomically, so going
back to a previous model is instant if its copy still exists.
"""
from __future__ import annotations

import argparse
import hashlib
import logging
import os
import re
from collections.abc import Callable, Iterable

from qdrant_client import QdrantClient
from qdrant_client.models import PointStruct

from core.collections import COLLECTIONS, DENSE_VECTOR_NAME, SPARSE_VECTOR_NAME
from core.embeddings import EmbeddingService
from core.indexer import (
    TAGGED_COLLECTIONS,
    create_hybrid_collection,
    point_alias,
    relative_to_root,
    resolve_physical,
)
from core.sparse import bm25_sparse

logger = logging.getLogger("mnemos.embedding_migration")

# The deduplicator embeds memories without a title; everything else is framed
# with its path. A copy must match what the live write path would produce.
_UNTITLED_COLLECTIONS = ("mnemos_memory",)


def physical_name(logical: str, model_name: str, dim: int) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", model_name.rsplit("/", 1)[-1].lower()).strip("-")
    return f"{logical}__{slug}_{dim}"


def _fingerprints(qdrant: QdrantClient, name: str) -> dict[str | int, str]:
    """id → digest of what was embedded, to find points that changed since a copy."""
    out: dict[str | int, str] = {}
    offset = None
    while True:
        points, offset = qdrant.scroll(
            name, limit=1000, offset=offset, with_payload=["content", "last_indexed_at"],
            with_vectors=False,
        )
        for p in points:
            payload = p.payload or {}
            raw = f"{payload.get('last_indexed_at', '')}|{payload.get('content', '')}"
            out[p.id] = hashlib.sha1(raw.encode()).hexdigest()
        if offset is None:
            return out


def _batches(items: list, size: int) -> Iterable[list]:
    for i in range(0, len(items), size):
        yield items[i:i + size]


def sync(
    qdrant: QdrantClient,
    logical: str,
    embeddings: EmbeddingService,
    codebase_root: str = "/data/codebase",
    batch_size: int = 128,
    progress: Callable[[str], None] = logger.info,
) -> dict[str, int]:
    """Bring `logical`'s copy for `embeddings.model_name` up to date with it."""
    source = resolve_physical(qdrant, logical)
    if source is None:
        progress(f"{logical}: absent, nothing to copy")
        return {"reembedded": 0, "deleted": 0}
    target = physical_name(logical, embeddings.model_name, embeddings.dimension)
    if source == target:
        progress(f"{logical}: already served by {target}")
        return {"reembedded": 0, "deleted": 0}
    if not qdrant.collection_exists(target):
        create_hybrid_collection(qdrant, target, embeddings.dimension, logical in TAGGED_COLLECTIONS)

    wanted = _fingerprints(qdrant, source)
    have = _fingerprints(qdrant, target)
    todo = [pid for pid, fp in wanted.items() if have.get(pid) != fp]
    gone = [pid for pid in have if pid not in wanted]

    done = 0
    for ids in _batches(todo, batch_size):
        points = qdrant.retrieve(source, ids=ids, with_payload=True, with_vectors=True)
        texts = [(p.payload or {}).get("content", "") for p in points]
        titles = (
            None if logical in _UNTITLED_COLLECTIONS
            else [relative_to_root((p.payload or {}).get("file_path", ""), codebase_root) for p in points]
        )
        dense = embeddings.embed_documents(texts, titles=titles)
        qdrant.upsert(target, points=[
            PointStruct(
                id=p.id,
                vector={DENSE_VECTOR_NAME: d, SPARSE_VECTOR_NAME: _sparse_of(p, text)},
                payload=p.payload,
            )
            for p, d, text in zip(points, dense, texts)
        ])
        done += len(points)
        progress(f"{logical}: {done}/{len(todo)} re-embedded into {target}")
    if gone:
        qdrant.delete(target, points_selector=gone)
    progress(f"{logical}: {target} in sync ({len(todo)} re-embedded, {len(gone)} deleted)")
    return {"reembedded": len(todo), "deleted": len(gone)}


def _sparse_of(point, text: str):
    # Legacy unnamed-dense collections carry no sparse vector: compute it.
    vectors = point.vector if isinstance(point.vector, dict) else {}
    return vectors.get(SPARSE_VECTOR_NAME) or bm25_sparse(text)


def switch(
    qdrant: QdrantClient,
    logical: str,
    model_name: str,
    snapshot: bool = True,
    progress: Callable[[str], None] = logger.info,
) -> str:
    """Serve `logical` from its copy for `model_name`. Returns that copy's name."""
    prefix = physical_name(logical, model_name, 0).rsplit("_", 1)[0] + "_"
    copies = [c.name for c in qdrant.get_collections().collections if c.name.startswith(prefix)]
    if len(copies) != 1:
        raise RuntimeError(f"{logical}: expected one copy named {prefix}<dim>, found {copies}; run sync first")
    target = copies[0]

    source = resolve_physical(qdrant, logical)
    if source == logical:
        # A real collection holds the name. Its copy must be complete before it goes.
        have, want = qdrant.count(target).count, qdrant.count(logical).count
        if have != want:
            raise RuntimeError(f"{logical}: {target} has {have} points, {logical} {want}; run sync again")
        if snapshot:
            snap = qdrant.create_snapshot(logical)
            progress(f"{logical}: snapshot {snap.name if snap else '?'} taken")
        qdrant.delete_collection(logical)
    point_alias(qdrant, logical, target)
    progress(f"{logical} → {target}")
    return target


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        prog="python -m core.embedding_migration",
        description="Move every collection to a new embedding model (sync, then switch).",
    )
    parser.add_argument("command", choices=["sync", "switch"])
    parser.add_argument("--model", required=True, help="embedding model to move to")
    parser.add_argument("--collections", nargs="*", default=[c.name for c in COLLECTIONS])
    parser.add_argument("--device", default=None, help="sync only: cpu, mps, cuda (default: auto)")
    parser.add_argument("--batch-size", type=int, default=128)
    parser.add_argument("--no-snapshot", action="store_true", help="switch only: skip the safety snapshot")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    # One line per Qdrant request would bury the progress lines.
    logging.getLogger("httpx").setLevel(logging.WARNING)

    qdrant = QdrantClient(
        host=os.environ.get("QDRANT_HOST", "localhost"),
        port=int(os.environ.get("QDRANT_PORT", "6333")),
        timeout=300,
    )
    if args.command == "sync":
        embeddings = EmbeddingService(args.model, device=args.device)
        for name in args.collections:
            sync(qdrant, name, embeddings, os.environ.get("CODEBASE_PATH", "/data/codebase"), args.batch_size)
    else:
        for name in args.collections:
            switch(qdrant, name, args.model, snapshot=not args.no_snapshot)


if __name__ == "__main__":
    main()
