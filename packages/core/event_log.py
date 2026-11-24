"""Append-mostly event logs in vectorless Qdrant collections.

The audit trail (`mnemos_audit`) and the indexing activity
(`mnemos_index_events`) are both a stream of small JSON records every pod
writes and the dashboard reads newest first. Qdrant already holds the shared
configuration, so they live there too: no new store to run, and every pod
sees every record.

Each record carries `ts` (epoch seconds, float) for ordering, paging and
retention, and `at` (ISO 8601, UTC) for display. Records older than
`retention_days` are deleted by `prune`, which every pod may run: deleting by
filter is idempotent.
"""
from __future__ import annotations

import logging
import threading
import time
import uuid
from collections.abc import Iterable
from datetime import UTC, datetime
from typing import Any

from qdrant_client.models import (
    FieldCondition,
    Filter,
    FilterSelector,
    KeywordIndexParams,
    MatchAny,
    MatchValue,
    OrderBy,
    PayloadSchemaType,
    PointStruct,
    Range,
)

logger = logging.getLogger("mnemos.event_log")

_MAX_PAGE = 200


class EventLog:
    def __init__(self, qdrant: Any, collection: str, keyword_fields: Iterable[str], retention_days: int) -> None:
        self._qdrant = qdrant
        self.collection = collection
        self._keywords = tuple(keyword_fields)
        # Read at every prune: the dashboard changes it live.
        self.retention_days = retention_days
        # `ts` doubles as the paging cursor (`before` is exclusive), so two
        # records of this process never share one; across pods a microsecond
        # tie is left to chance.
        self._last_ts = 0.0
        self._ts_lock = threading.Lock()

    def ensure_collection(self) -> None:
        if not self._qdrant.collection_exists(self.collection):
            try:
                self._qdrant.create_collection(collection_name=self.collection, vectors_config={})
            except Exception:
                # Another pod starting at the same time created it first.
                if not self._qdrant.collection_exists(self.collection):
                    raise
        # Ordering and range filters need a numeric index on `ts`; the
        # dashboard filters and facets on the keyword fields.
        indexes = [("ts", PayloadSchemaType.FLOAT)] + [
            (field, KeywordIndexParams(type=PayloadSchemaType.KEYWORD)) for field in self._keywords
        ]
        for field, schema in indexes:
            try:
                self._qdrant.create_payload_index(
                    collection_name=self.collection, field_name=field, field_schema=schema,
                )
            except Exception as exc:
                if "already exists" not in str(exc).lower():
                    logger.warning("payload index %s on %s not created: %s", field, self.collection, exc)

    def record(self, entry: dict[str, Any], *, wait: bool = True) -> str:
        """Store `entry` stamped with `ts` and `at`; return its id."""
        point_id = str(uuid.uuid4())
        with self._ts_lock:
            now = max(time.time(), self._last_ts + 1e-6)
            self._last_ts = now
        payload = {
            **entry,
            "id": point_id,
            "ts": now,
            "at": datetime.fromtimestamp(now, UTC).isoformat(timespec="seconds"),
        }
        self._qdrant.upsert(
            collection_name=self.collection,
            points=[PointStruct(id=point_id, vector={}, payload=payload)],
            wait=wait,
        )
        return point_id

    def update(self, record_id: str, fields: dict[str, Any]) -> None:
        """Merge `fields` into a record (a job's progress); its `ts` stays."""
        self._qdrant.set_payload(collection_name=self.collection, payload=fields, points=[record_id])

    def get(self, record_id: str) -> dict[str, Any] | None:
        records = self._qdrant.retrieve(collection_name=self.collection, ids=[record_id], with_payload=True)
        return dict(records[0].payload or {}) if records else None

    def query(
        self,
        filters: dict[str, str | list[str]] | None = None,
        *,
        since: float | None = None,
        before: float | None = None,
        limit: int = 50,
    ) -> tuple[list[dict[str, Any]], float | None]:
        """Newest first. `before` is the cursor: the `ts` of the last record of
        the previous page. Returns the page and the next cursor (None at the end)."""
        limit = max(1, min(limit, _MAX_PAGE))
        points, _ = self._qdrant.scroll(
            collection_name=self.collection,
            scroll_filter=self._filter(filters, since=since, before=before),
            order_by=OrderBy(key="ts", direction="desc"),
            limit=limit + 1,
            with_payload=True,
            with_vectors=False,
        )
        items = [dict(p.payload or {}) for p in points]
        if len(items) > limit:
            return items[:limit], items[limit - 1]["ts"]
        return items, None

    def count(self, filters: dict[str, str | list[str]] | None = None, *, since: float | None = None) -> int:
        return self._qdrant.count(
            collection_name=self.collection, count_filter=self._filter(filters, since=since), exact=True,
        ).count

    def values(self, field: str, limit: int = 100) -> list[str]:
        """Distinct values of a keyword field, most frequent first; [] when Qdrant cannot facet."""
        try:
            response = self._qdrant.facet(collection_name=self.collection, key=field, limit=limit)
        except Exception:
            return []
        return [str(hit.value) for hit in response.hits]

    def prune(self, now: float | None = None) -> None:
        """Delete what is older than `retention_days`."""
        cutoff = (now if now is not None else time.time()) - self.retention_days * 86400
        self._qdrant.delete(
            collection_name=self.collection,
            points_selector=FilterSelector(filter=Filter(must=[FieldCondition(key="ts", range=Range(lt=cutoff))])),
        )

    @staticmethod
    def _filter(
        filters: dict[str, str | list[str]] | None, *, since: float | None = None, before: float | None = None,
    ) -> Filter | None:
        must: list = []
        for field, value in (filters or {}).items():
            if value in (None, "", []):
                continue
            match = MatchAny(any=list(value)) if isinstance(value, list) else MatchValue(value=value)
            must.append(FieldCondition(key=field, match=match))
        if since is not None or before is not None:
            must.append(FieldCondition(key="ts", range=Range(gte=since, lt=before)))
        return Filter(must=must) if must else None
