from __future__ import annotations

import time
from typing import Iterable

import httpx

from eval.schema import GoldenItem, QueryResult


class EvalRunner:
    """Runs a golden set against a live Mnemos server via the REST API."""

    def __init__(self, mnemos_url: str, limit: int = 10, timeout: float = 120.0) -> None:
        self._url = mnemos_url.rstrip("/")
        self._limit = limit
        self._timeout = timeout

    def run(self, items: Iterable[GoldenItem]) -> list[QueryResult]:
        results: list[QueryResult] = []
        with httpx.Client(timeout=self._timeout) as client:
            for item in items:
                results.append(self._run_one(client, item))
        return results

    def _run_one(self, client: httpx.Client, item: GoldenItem) -> QueryResult:
        endpoint, payload = self._build_request(item)
        started = time.perf_counter()
        response = client.post(f"{self._url}{endpoint}", json=payload)
        latency_ms = (time.perf_counter() - started) * 1000.0
        response.raise_for_status()
        data = response.json()
        hits = data.get("results", [])
        return QueryResult(
            item_id=item.id,
            retrieved_files=[h.get("file_path", "") for h in hits],
            retrieved_collections=[h.get("collection", "") for h in hits],
            retrieved_scores=[float(h.get("score", 0.0)) for h in hits],
            latency_ms=latency_ms,
            raw_top_k=hits,
        )

    def _build_request(self, item: GoldenItem) -> tuple[str, dict]:
        # /api/search-skills returns SkillResult (no file_path), so route skill_discovery
        # through /api/search restricted to mnemos_skills — keeps file_path matching consistent.
        if item.intent == "skill_discovery":
            return "/api/search", {
                "query": item.query,
                "limit": self._limit,
                "collections": ["mnemos_skills"],
            }
        if item.intent == "memory_recall":
            return "/api/search-memory", {"query": item.query, "limit": self._limit}
        if item.intent == "code_search":
            return "/api/search-code", {"query": item.query, "limit": self._limit}
        payload: dict = {"query": item.query, "limit": self._limit}
        if item.expected_collections:
            payload["collections"] = item.expected_collections
        return "/api/search", payload
