"""Thin HTTP client for the Mnemos endpoints a connector needs."""

from __future__ import annotations

from urllib.parse import quote

import httpx

from connectors.transport import DEFAULT_RETRY, RetryPolicy, request

KNOWLEDGE_COLLECTION = "mnemos_knowledge"

# Pushing a page means chunking + embedding (and, when enabled, one LLM call
# per chunk for contextual chunking) — far slower than reading a source API.
MNEMOS_TIMEOUT = httpx.Timeout(300.0, connect=10.0)


class MnemosClient:
    """Push, list and delete files of one collection (``mnemos_knowledge`` by default) over HTTP.

    ``client`` carries the auth header (``Authorization: Bearer`` when
    ``MNEMOS_API_KEY`` is set); this class only knows the routes.
    """

    def __init__(
        self,
        base_url: str,
        client: httpx.Client,
        *,
        collection: str = KNOWLEDGE_COLLECTION,
        retry: RetryPolicy = DEFAULT_RETRY,
    ) -> None:
        self._base = base_url.rstrip("/")
        self._client = client
        self._collection = collection
        self._retry = retry

    @property
    def collection(self) -> str:
        return self._collection

    def for_collection(self, collection: str) -> MnemosClient:
        """Same server and credentials, another collection."""
        return MnemosClient(self._base, self._client, collection=collection, retry=self._retry)

    def list_files(self, tag: str) -> dict[str, float | None]:
        """Every file indexed under ``tag`` → its stored ``file_mtime``."""
        response = request(
            self._client,
            "GET",
            f"{self._base}/api/index/{self._collection}/files",
            params={"tag": tag},
            retry=self._retry,
        )
        response.raise_for_status()
        files = response.json().get("files") or {}
        return {str(path): mtime for path, mtime in files.items()}

    def push(
        self,
        *,
        file_path: str,
        content: str,
        tags: list[str],
        source_url: str,
        file_mtime: float | None,
    ) -> None:
        """Index one document; the server replaces any previous chunks of it."""
        payload: dict = {
            "file_path": file_path,
            "collection": self._collection,
            "content": content,
            "tags": tags,
            "source_url": source_url,
        }
        if file_mtime is not None:
            payload["file_mtime"] = file_mtime
        response = request(
            self._client, "POST", f"{self._base}/api/index", json=payload, retry=self._retry
        )
        response.raise_for_status()

    def push_many(self, docs: list[dict]) -> list[dict] | None:
        """Index several documents in one request, embedded together; one result each.

        ``docs`` items take the keys of :meth:`push`. None when the server
        predates ``/api/index/batch``: the caller then pushes one by one.
        """
        response = request(
            self._client, "POST", f"{self._base}/api/index/batch",
            json={"collection": self._collection, "items": docs}, retry=self._retry,
        )
        if response.status_code in (404, 405):
            return None
        response.raise_for_status()
        return response.json()["results"]

    def delete(self, file_path: str) -> None:
        """Remove every chunk of ``file_path`` from the collection."""
        response = request(
            self._client,
            "DELETE",
            f"{self._base}/api/index/{self._collection}/{quote(file_path, safe='/~')}",
            retry=self._retry,
        )
        response.raise_for_status()
