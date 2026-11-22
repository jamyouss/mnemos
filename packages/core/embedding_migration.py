"""Rebuild collections for a new embedding model or chunk size, without downtime.

Run it where the server runs (same Qdrant, same codebase mount), or on the host
against the same Qdrant with `--device mps`:

    python -m core.embedding_migration sync   --model google/embeddinggemma-2
    python -m core.embedding_migration switch --model google/embeddinggemma-2

`sync` copies each collection into `<name>__<model-slug>_<dim>`, re-embedding
the dense vector from the stored `content` and keeping sparse vectors and
payloads as they are, so memories and connector pages, which have no source
file, survive too. It is repeatable: a second run only re-embeds what changed
since the first, which keeps the final catch-up short.

`--max-chunk-chars N` also re-splits every stored chunk longer than N with the
indexer's own splitter, into `<name>__<model-slug>_<dim>_c<N>`. Point ids are
the ones the indexer gives, so later edits overwrite them in place. Chunks
that stay whole keep their vector when the source already uses the model.
Memories are never re-split.

`switch` turns each logical name into an alias of its copy. The first switch
snapshots and deletes the original collection, since an alias cannot share a
collection's name. Later switches only move the alias, atomically, so going
back to a previous copy is instant while it still exists.
"""
from __future__ import annotations

import argparse
import hashlib
import logging
import os
import re
from collections import defaultdict
from collections.abc import Callable, Iterable

from qdrant_client import QdrantClient
from qdrant_client.models import FieldCondition, Filter, FilterSelector, MatchAny, PointStruct

from core.chunkers.splitter import split_long_chunks
from core.collections import COLLECTIONS, DENSE_VECTOR_NAME, SPARSE_VECTOR_NAME
from core.embeddings import EmbeddingService
from core.indexer import (
    TAGGED_COLLECTIONS,
    chunk_point_id,
    create_hybrid_collection,
    point_alias,
    relative_to_root,
    resolve_physical,
)
from core.sparse import bm25_sparse

logger = logging.getLogger("mnemos.embedding_migration")

# Memories are written whole and embedded without a title by the deduplicator;
# a copy must match what that write path produces, and is never re-split.
_MEMORY_COLLECTIONS = ("mnemos_memory",)


def _slug(model_name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", model_name.rsplit("/", 1)[-1].lower()).strip("-")


def physical_name(logical: str, model_name: str, dim: int, max_chunk_chars: int | None = None) -> str:
    suffix = f"_c{max_chunk_chars}" if max_chunk_chars else ""
    return f"{logical}__{_slug(model_name)}_{dim}{suffix}"


def _batches(items: list, size: int) -> Iterable[list]:
    for i in range(0, len(items), size):
        yield items[i:i + size]


def _file_filter(files: list[str]) -> Filter:
    return Filter(must=[FieldCondition(key="file_path", match=MatchAny(any=files))])


def sync(
    qdrant: QdrantClient,
    logical: str,
    embeddings: EmbeddingService,
    codebase_root: str = "/data/codebase",
    batch_size: int = 128,
    max_chunk_chars: int | None = None,
    progress: Callable[[str], None] = logger.info,
) -> dict[str, int]:
    """Bring `logical`'s copy for this model (and chunk size) up to date with it."""
    if max_chunk_chars and logical in _MEMORY_COLLECTIONS:
        progress(f"{logical}: memories are never re-split, skipped")
        return {"reembedded": 0, "deleted": 0}
    source = resolve_physical(qdrant, logical)
    if source is None:
        progress(f"{logical}: absent, nothing to copy")
        return {"reembedded": 0, "deleted": 0}
    target = physical_name(logical, embeddings.model_name, embeddings.dimension, max_chunk_chars)
    if source == target:
        progress(f"{logical}: already served by {target}")
        return {"reembedded": 0, "deleted": 0}
    if not qdrant.collection_exists(target):
        create_hybrid_collection(qdrant, target, embeddings.dimension, logical in TAGGED_COLLECTIONS)

    if max_chunk_chars:
        counts = _sync_files(qdrant, logical, source, target, embeddings, codebase_root,
                             max_chunk_chars, progress)
    else:
        counts = _sync_points(qdrant, logical, source, target, embeddings, codebase_root,
                              batch_size, progress)
    progress(f"{logical}: {target} in sync ({counts['reembedded']} re-embedded, {counts['deleted']} deleted)")
    return counts


# --- Same chunks, new model: point by point ----------------------------------

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


def _sync_points(qdrant, logical, source, target, embeddings, codebase_root, batch_size, progress):
    wanted = _fingerprints(qdrant, source)
    have = _fingerprints(qdrant, target)
    todo = [pid for pid, fp in wanted.items() if have.get(pid) != fp]
    gone = [pid for pid in have if pid not in wanted]

    done = 0
    for ids in _batches(todo, batch_size):
        points = qdrant.retrieve(source, ids=ids, with_payload=True, with_vectors=True)
        texts = [(p.payload or {}).get("content", "") for p in points]
        titles = (
            None if logical in _MEMORY_COLLECTIONS
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
    return {"reembedded": len(todo), "deleted": len(gone)}


def _sparse_of(point, text: str):
    # Legacy unnamed-dense collections carry no sparse vector: compute it.
    vectors = point.vector if isinstance(point.vector, dict) else {}
    return vectors.get(SPARSE_VECTOR_NAME) or bm25_sparse(text)


# --- New chunk size: file by file --------------------------------------------

def _file_stamps(qdrant: QdrantClient, name: str) -> dict[str, str]:
    """file_path → when it was last indexed. All chunks of a file share it, and
    a copy keeps the source's, so equal stamps mean the copy is current."""
    out: dict[str, str] = {}
    offset = None
    while True:
        points, offset = qdrant.scroll(
            name, limit=2000, offset=offset, with_payload=["file_path", "last_indexed_at"],
            with_vectors=False,
        )
        for p in points:
            payload = p.payload or {}
            path, stamp = payload.get("file_path", ""), str(payload.get("last_indexed_at", ""))
            out[path] = max(out.get(path, ""), stamp)
        if offset is None:
            return out


def _points_of(qdrant: QdrantClient, name: str, files: list[str], dense: bool) -> list:
    out, offset = [], None
    while True:
        points, offset = qdrant.scroll(
            name, scroll_filter=_file_filter(files), limit=1000, offset=offset,
            with_payload=True, with_vectors=[DENSE_VECTOR_NAME] if dense else False,
        )
        out += points
        if offset is None:
            return out


def _sync_files(qdrant, logical, source, target, embeddings, codebase_root, max_chunk_chars, progress,
                files_per_batch: int = 32):
    wanted = _file_stamps(qdrant, source)
    have = _file_stamps(qdrant, target)
    todo = [f for f, stamp in wanted.items() if have.get(f) != stamp]
    gone = [f for f in have if f not in wanted]
    # A chunk the split leaves whole needs no new vector if the model is the same.
    same_model = re.fullmatch(
        re.escape(physical_name(logical, embeddings.model_name, embeddings.dimension)) + r"(_c\d+)?", source
    ) is not None

    reembedded = deleted = 0
    for n, files in enumerate(_batches(todo, files_per_batch), 1):
        by_file: dict[str, list] = defaultdict(list)
        for p in _points_of(qdrant, source, files, dense=same_model):
            by_file[p.payload["file_path"]].append(p)

        rows: list[list] = []  # [point id, dense vector or None, payload]
        for path, points in by_file.items():
            points.sort(key=lambda p: p.payload.get("chunk_index", 0))
            known = {p.payload.get("content", ""): p.vector[DENSE_VECTOR_NAME] for p in points} if same_model else {}
            for chunk in split_long_chunks([dict(p.payload) for p in points], max_chunk_chars):
                rows.append([chunk_point_id(path, chunk["chunk_index"]), known.get(chunk["content"]), chunk])

        missing = [r for r in rows if r[1] is None]
        vectors = embeddings.embed_documents(
            [r[2].get("content", "") for r in missing],
            titles=[relative_to_root(r[2]["file_path"], codebase_root) for r in missing],
        )
        for row, vector in zip(missing, vectors):
            row[1] = vector
        reembedded += len(missing)

        before = qdrant.count(target, count_filter=_file_filter(files)).count
        qdrant.delete(target, points_selector=FilterSelector(filter=_file_filter(files)))
        deleted += before
        for chunk_rows in _batches(rows, 64):
            qdrant.upsert(target, points=[
                PointStruct(
                    id=pid,
                    vector={DENSE_VECTOR_NAME: vector, SPARSE_VECTOR_NAME: bm25_sparse(payload.get("content", ""))},
                    payload=payload,
                )
                for pid, vector, payload in chunk_rows
            ])
        progress(f"{logical}: {min(n * files_per_batch, len(todo))}/{len(todo)} files, "
                 f"{reembedded} chunks embedded, into {target}")
    for files in _batches(gone, 256):
        deleted += qdrant.count(target, count_filter=_file_filter(files)).count
        qdrant.delete(target, points_selector=FilterSelector(filter=_file_filter(files)))
    return {"reembedded": reembedded, "deleted": deleted}


# --- Switch --------------------------------------------------------------------

def switch(
    qdrant: QdrantClient,
    logical: str,
    model_name: str,
    max_chunk_chars: int | None = None,
    snapshot: bool = True,
    progress: Callable[[str], None] = logger.info,
) -> str:
    """Serve `logical` from its copy for `model_name` (and chunk size). Returns that copy's name."""
    if max_chunk_chars and logical in _MEMORY_COLLECTIONS:
        max_chunk_chars = None  # memories have one copy per model
    suffix = f"_c{max_chunk_chars}" if max_chunk_chars else ""
    pattern = re.compile(re.escape(f"{logical}__{_slug(model_name)}_") + r"\d+" + re.escape(suffix))
    copies = [c.name for c in qdrant.get_collections().collections if pattern.fullmatch(c.name)]
    if len(copies) != 1:
        raise RuntimeError(f"{logical}: expected one copy matching {pattern.pattern}, found {copies}; run sync first")
    target = copies[0]

    source = resolve_physical(qdrant, logical)
    if source == target:
        progress(f"{logical}: already served by {target}")
        return target
    if source == logical:
        # A real collection holds the name. Its copy must be complete before it goes.
        # Re-split copies hold more points than their source: compare files instead.
        if max_chunk_chars:
            behind = _file_stamps(qdrant, target) != _file_stamps(qdrant, logical)
        else:
            behind = qdrant.count(target).count != qdrant.count(logical).count
        if behind:
            raise RuntimeError(f"{logical}: {target} is behind {logical}; run sync again")
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
        description="Rebuild collections for a new embedding model or chunk size (sync, then switch).",
    )
    parser.add_argument("command", choices=["sync", "switch"])
    parser.add_argument("--model", required=True, help="embedding model of the copy")
    parser.add_argument("--max-chunk-chars", type=int, default=None,
                        help="re-split chunks longer than this (match MNEMOS_MAX_CHUNK_CHARS)")
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
        api_key=os.environ.get("QDRANT_API_KEY") or None,
        https=os.environ.get("QDRANT_HTTPS", "").lower() in ("1", "true", "yes"),
        timeout=300,
    )
    if args.command == "sync":
        embeddings = EmbeddingService(args.model, device=args.device)
        for name in args.collections:
            sync(qdrant, name, embeddings, os.environ.get("CODEBASE_PATH", "/data/codebase"),
                 args.batch_size, args.max_chunk_chars)
    else:
        for name in args.collections:
            switch(qdrant, name, args.model, args.max_chunk_chars, snapshot=not args.no_snapshot)


if __name__ == "__main__":
    main()
