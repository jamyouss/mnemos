from __future__ import annotations

import logging
import time
import uuid
from collections.abc import Iterable
from datetime import datetime, timezone

from qdrant_client import QdrantClient
from qdrant_client.models import (
    CreateAlias,
    CreateAliasOperation,
    DeleteAlias,
    DeleteAliasOperation,
    Distance,
    FieldCondition,
    Filter,
    FilterSelector,
    KeywordIndexParams,
    MatchValue,
    Modifier,
    PayloadSchemaType,
    PointStruct,
    SparseVectorParams,
    VectorParams,
)

from core.chunkers.fallback_chunker import FallbackChunker
from core.chunkers.go_chunker import GoChunker
from core.chunkers.markdown_chunker import MarkdownChunker
from core.chunkers.splitter import split_long_chunks
from core.chunkers.tabular_chunker import TabularChunker
from core.chunkers.vue_chunker import VueChunker
from core.collections import DENSE_VECTOR_NAME, SPARSE_VECTOR_NAME, TAGS_PAYLOAD_FIELD
from core.contextual import ContextualEnricher
from core.embeddings import EmbeddingService
from core.path_filter import should_skip_path
from core.projects import PathExcludes, PathTags, detect_excludes, detect_tags
from core.skills import skill_payload
from core.sparse import bm25_sparse

_logger = logging.getLogger(__name__)


def resolve_skip(
    file_path: str,
    codebase_root: str = "/data/codebase",
    path_excludes: PathExcludes | None = None,
    extra_exts: Iterable[str] = (),
    extra_dirs: Iterable[str] = (),
    declared_prefixes: Iterable[str] | None = None,
) -> bool:
    """Should this file be left out of the index?

    Unions the three sources: the built-in policy in :mod:`core.path_filter`,
    the per-prefix rules loaded from ``config/projects.yaml``, and any caller
    extras. They add up; none overrides another.

    ``declared_prefixes`` turns on allowlist mode: anything under the codebase
    mount that no declared prefix covers is skipped. The watcher walks the
    whole mount and knows nothing of which repos you meant to index, so
    without this it happily indexes trees you deliberately left out.

    Kept module-level and stateless so callers without an ``Indexer`` — the
    offline purge script, for one — get the same answer as the ingest path
    instead of re-deriving the union and drifting from it.
    """
    root = codebase_root.rstrip("/")
    under_codebase = file_path.startswith(root + "/")
    rel = file_path[len(root) + 1:] if under_codebase else file_path

    # Allowlist mode: only paths declared in config/projects.yaml are indexed.
    # Scoped to the codebase mount on purpose — skills and docs live under a
    # different root and must not be caught by it.
    if declared_prefixes is not None and under_codebase:
        if not any(
            rel.startswith(pfx if pfx.endswith("/") else pfx + "/")
            for pfx in declared_prefixes
        ):
            return True

    config = detect_excludes(rel, path_excludes or {})
    return should_skip_path(
        file_path,
        extra_exts=[*config["exts"], *extra_exts],
        extra_dirs=[*config["dirs"], *extra_dirs],
    )


def relative_to_root(file_path: str, codebase_root: str) -> str:
    """Strip the codebase mount root. Also the document title the embedder sees."""
    root = codebase_root.rstrip("/")
    if file_path.startswith(root + "/"):
        return file_path[len(root) + 1:]
    return file_path


# Collections whose payload is filtered by `tags` at query time. We
# create the keyword payload index on those at startup so the filter scales.
TAGGED_COLLECTIONS = ("mnemos_code", "mnemos_memory", "mnemos_knowledge")


def create_hybrid_collection(
    qdrant: QdrantClient, name: str, vector_size: int, tagged: bool
) -> None:
    """Create `name` with the hybrid schema (dense + sparse BM25).

    `tagged` adds a keyword payload index on `tags` so tag filters stay O(log n).
    """
    qdrant.create_collection(
        collection_name=name,
        vectors_config={
            DENSE_VECTOR_NAME: VectorParams(size=vector_size, distance=Distance.COSINE),
        },
        sparse_vectors_config={
            SPARSE_VECTOR_NAME: SparseVectorParams(modifier=Modifier.IDF),
        },
    )
    if tagged:
        _ensure_tags_payload_index(qdrant, name)


def _ensure_tags_payload_index(qdrant: QdrantClient, name: str) -> None:
    """Idempotently create a keyword payload index on the `tags` field."""
    try:
        qdrant.create_payload_index(
            collection_name=name,
            field_name=TAGS_PAYLOAD_FIELD,
            field_schema=KeywordIndexParams(type=PayloadSchemaType.KEYWORD),
        )
    except Exception:
        # Qdrant raises if the index already exists; that's fine.
        pass


def dense_vector_size(qdrant: QdrantClient, name: str) -> int:
    """Size of the dense vector of `name` (a collection or an alias)."""
    vectors = qdrant.get_collection(name).config.params.vectors
    if isinstance(vectors, dict):
        return vectors[DENSE_VECTOR_NAME].size
    return vectors.size  # legacy unnamed-dense schema


def resolve_physical(qdrant: QdrantClient, name: str) -> str | None:
    """The collection `name` designates: its alias target, itself, or None."""
    for alias in qdrant.get_aliases().aliases:
        if alias.alias_name == name:
            return alias.collection_name
    return name if qdrant.collection_exists(name) else None


def point_alias(qdrant: QdrantClient, alias: str, target: str) -> None:
    """Point `alias` at `target` in one atomic update, replacing any previous target."""
    ops: list = []
    if any(a.alias_name == alias for a in qdrant.get_aliases().aliases):
        ops.append(DeleteAliasOperation(delete_alias=DeleteAlias(alias_name=alias)))
    ops.append(CreateAliasOperation(create_alias=CreateAlias(collection_name=target, alias_name=alias)))
    qdrant.update_collection_aliases(change_aliases_operations=ops)


def chunk_point_id(file_path: str, chunk_index: int) -> str:
    """Stable id of a file's n-th chunk, so re-indexing a file overwrites it."""
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"{file_path}::{chunk_index}"))


class Indexer:
    def __init__(
        self,
        qdrant_client: QdrantClient,
        embedding_service: EmbeddingService,
        contextual_enricher: ContextualEnricher | None = None,
        path_tags: PathTags | None = None,
        path_excludes: PathExcludes | None = None,
        only_declared_paths: bool = False,
        codebase_root: str = "/data/codebase",
        max_chunk_chars: int = 0,
    ) -> None:
        self._qdrant = qdrant_client
        self._embeddings = embedding_service
        self._contextual = contextual_enricher
        self._path_tags = path_tags or {}
        self._path_excludes = path_excludes or {}
        self._only_declared_paths = only_declared_paths
        self._codebase_root = codebase_root.rstrip("/")
        self._max_chunk_chars = max_chunk_chars
        self._go_chunker = GoChunker()
        self._vue_chunker = VueChunker()
        self._md_chunker = MarkdownChunker()
        self._fallback_chunker = FallbackChunker()
        self._tabular_chunker = TabularChunker(fallback=self._fallback_chunker)

    def ensure_collection(self, collection_name: str) -> None:
        """Create the collection if missing, hybrid and sized for the current model.

        `collection_exists` also matches aliases; `get_collections()` does not,
        and an alias left by an embedding migration would otherwise be
        "missing" and recreated over.
        """
        tagged = collection_name in TAGGED_COLLECTIONS
        if not self._qdrant.collection_exists(collection_name):
            create_hybrid_collection(
                self._qdrant, collection_name, self._embeddings.dimension, tagged
            )
        elif tagged:
            _ensure_tags_payload_index(self._qdrant, collection_name)

    def is_hybrid(self, collection_name: str) -> bool:
        """Return True if the collection already uses named hybrid (dense + sparse) vectors."""
        try:
            info = self._qdrant.get_collection(collection_name)
        except Exception:
            return False
        vectors = info.config.params.vectors
        sparse = info.config.params.sparse_vectors
        return isinstance(vectors, dict) and DENSE_VECTOR_NAME in vectors and sparse is not None

    def recreate_collection_hybrid(self, collection_name: str) -> None:
        """Drop and recreate the collection with the hybrid schema. Destructive.

        Behind an alias, the physical collection is the one recreated, and the
        alias is put back: Qdrant drops aliases with their collection.
        """
        physical = resolve_physical(self._qdrant, collection_name)
        if physical is not None:
            self._qdrant.delete_collection(collection_name=physical)
        target = physical or collection_name
        create_hybrid_collection(
            self._qdrant, target, self._embeddings.dimension,
            collection_name in TAGGED_COLLECTIONS,
        )
        if target != collection_name:
            point_alias(self._qdrant, collection_name, target)

    def _relative_path(self, file_path: str) -> str:
        """Strip the codebase mount root, so config prefixes match."""
        return relative_to_root(file_path, self._codebase_root)

    def _resolve_excludes(self, file_path: str) -> dict[str, list[str]]:
        """Per-prefix ignore rules from `config/projects.yaml` for this file."""
        return detect_excludes(self._relative_path(file_path), self._path_excludes)

    def should_skip(
        self,
        file_path: str,
        extra_exts: Iterable[str] = (),
        extra_dirs: Iterable[str] = (),
    ) -> bool:
        """Single entry point for "must this file be left out of the index?".

        Unions three sources — the built-in policy in :mod:`core.path_filter`,
        the per-prefix rules from ``config/projects.yaml``, and any caller
        extras (``mnemos reindex --exclude-ext/--exclude-dir``). They add up;
        none of them overrides another.

        Both ``index_file`` and the bulk-reindex walker go through here, so the
        resolution lives in one place. Re-deriving it at a call site is exactly
        the drift ``core.path_filter`` was created to end.
        """
        return resolve_skip(
            file_path,
            codebase_root=self._codebase_root,
            path_excludes=self._path_excludes,
            extra_exts=extra_exts,
            extra_dirs=extra_dirs,
            declared_prefixes=(
                list(self._path_tags) if self._only_declared_paths else None
            ),
        )

    def _resolve_tags(self, file_path: str, override: list[str] | None) -> list[str]:
        """Decide which tag list to write into a chunk's payload.

        Order:
            1. Explicit ``override`` (CLI flag / push API field).
            2. YAML / segment-based detection on the path relative to the
               codebase mount root.
        """
        if override:
            return list(override)
        return detect_tags(self._relative_path(file_path), overrides=self._path_tags)

    def index_file(
        self,
        content: str,
        file_path: str,
        collection: str,
        file_mtime: float | None = None,
        tags: list[str] | None = None,
        source_url: str | None = None,
    ) -> int:
        # Defense-in-depth: even if a caller (push API, hook) forgot to
        # pre-filter, vendored bundles / build outputs never reach the
        # embedder. Watcher and bulk reindex still pre-filter to avoid
        # the round-trip cost.
        if self.should_skip(file_path):
            _logger.debug("path_filter: skipping %s", file_path)
            return 0

        self.delete_file(file_path, collection)

        chunker = self._select_chunker(file_path)
        chunks = split_long_chunks(chunker.chunk(content, file_path), self._max_chunk_chars)

        if not chunks:
            return 0

        # Optional Anthropic-style contextual enrichment: prepend an LLM-generated
        # one-sentence preamble to each chunk before embedding + sparse indexing.
        if self._contextual is not None and self._contextual.enabled:
            chunks = self._contextual.enrich(content, chunks, file_path)

        # Resolve tags once per file. Only relevant for the code/memory
        # collections; skills/docs leave the field unset.
        resolved_tags: list[str] = (
            self._resolve_tags(file_path, tags)
            if collection in TAGGED_COLLECTIONS
            else []
        )

        # Skill name + description, so a search result can say which skill it
        # came from. Read at query time by SkillResult; nothing wrote them
        # before, which left mnemos_search_skills returning unnamed hits.
        skill_fields = (
            skill_payload(content, file_path)
            if collection == "mnemos_skills"
            else {}
        )

        texts = [c["content"] for c in chunks]
        dense_vectors = self._embeddings.embed_documents(
            texts, titles=[self._relative_path(file_path)] * len(texts)
        )
        sparse_vectors = [bm25_sparse(t) for t in texts]

        now = datetime.now(timezone.utc).isoformat()
        points = []
        for chunk, dense, sparse in zip(chunks, dense_vectors, sparse_vectors):
            point_id = chunk_point_id(file_path, chunk.get("chunk_index", 0))
            payload = {
                **chunk,
                **skill_fields,
                "last_indexed_at": now,
                "file_mtime": file_mtime or time.time(),
            }
            if resolved_tags:
                payload[TAGS_PAYLOAD_FIELD] = resolved_tags
            if source_url:
                payload["source_url"] = source_url
            points.append(
                PointStruct(
                    id=point_id,
                    vector={
                        DENSE_VECTOR_NAME: dense,
                        SPARSE_VECTOR_NAME: sparse,
                    },
                    payload=payload,
                )
            )

        # Batch upserts to stay under Qdrant payload size limits
        batch_size = 50
        for i in range(0, len(points), batch_size):
            self._qdrant.upsert(collection_name=collection, points=points[i:i + batch_size])
        return len(points)

    def delete_file(self, file_path: str, collection: str) -> None:
        self._qdrant.delete(
            collection_name=collection,
            points_selector=FilterSelector(
                filter=Filter(
                    must=[FieldCondition(key="file_path", match=MatchValue(value=file_path))]
                )
            ),
        )

    def list_files(self, collection: str, tag: str) -> dict[str, float | None]:
        """Every file indexed under `tag`, mapped to its stored `file_mtime`.

        Lets a connector skip pages that did not change and delete the ones
        that disappeared at the source, without keeping state of its own.
        """
        files: dict[str, float | None] = {}
        offset = None
        while True:
            points, offset = self._qdrant.scroll(
                collection_name=collection,
                scroll_filter=Filter(
                    must=[FieldCondition(key=TAGS_PAYLOAD_FIELD, match=MatchValue(value=tag))]
                ),
                with_payload=["file_path", "file_mtime"],
                with_vectors=False,
                limit=256,
                offset=offset,
            )
            for point in points:
                payload = point.payload or {}
                if payload.get("file_path"):
                    files[payload["file_path"]] = payload.get("file_mtime")
            if offset is None:
                return files

    def _select_chunker(self, file_path: str):
        if file_path.endswith(".go"):
            return self._go_chunker
        if file_path.endswith(".vue"):
            return self._vue_chunker
        if file_path.endswith((".csv", ".tsv")):
            return self._tabular_chunker
        if file_path.endswith(".md"):
            return self._md_chunker
        return self._fallback_chunker

