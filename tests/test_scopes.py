"""Tenant read/write scopes enforced on routes, search and MCP tools."""
from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import MagicMock

import mcp.types as types
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from mcp.server.lowlevel.server import request_ctx
from mcp.shared.context import RequestContext
from qdrant_client.models import FieldCondition, MatchAny

from core.models import DeduplicationResult
from server.api import api_router
from server.config import settings
from server.mcp_tools import _dispatch_tool, create_mcp_server
from server.principal import FULL_ACCESS, PRINCIPAL_SCOPE_KEY, Principal
from server.search import SearchService

ACME = Principal(name="acme-dev", admin=False, read=frozenset({"acme"}), write=frozenset({"acme"}))
READ_ONLY = Principal(name="viewer", admin=False, read=frozenset({"acme"}), write=frozenset())


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _scope_tags(query_filter) -> list[list[str]]:
    """Every MatchAny on `tags` in a filter's `must` list."""
    if query_filter is None:
        return []
    return [
        list(c.match.any)
        for c in query_filter.must or []
        if isinstance(c, FieldCondition) and c.key == "tags" and isinstance(c.match, MatchAny)
    ]


def _filters_by_collection(qdrant) -> dict[str, object]:
    """The prefetch filter of the last hybrid query sent to each collection."""
    out = {}
    for call in qdrant.query_points.call_args_list:
        prefetch = call.kwargs.get("prefetch") or []
        out[call.kwargs["collection_name"]] = prefetch[0].filter if prefetch else call.kwargs.get("query_filter")
    return out


def _hit(tags: list[str]):
    return MagicMock(
        score=0.9,
        payload={"content": "x", "file_path": "acme/a.go", "chunk_type": "function", "tags": tags},
    )


@pytest.fixture
def qdrant():
    client = MagicMock()
    client.scroll.return_value = ([], None)
    client.query_points.return_value = MagicMock(points=[])
    return client


@pytest.fixture
def search_service(qdrant):
    embeddings = MagicMock()
    embeddings.embed_query.return_value = [0.1] * 4
    return SearchService(qdrant_client=qdrant, embedding_service=embeddings)


@pytest.fixture
def app(qdrant, search_service):
    """The API router alone, with mocked state: no lifespan, no auth middleware."""
    application = FastAPI()
    application.include_router(api_router)
    application.state.qdrant = qdrant
    application.state.search_service = search_service
    application.state.indexer = MagicMock()
    application.state.indexer.index_file.return_value = 1
    application.state.deduplicator = MagicMock()
    application.state.deduplicator.deduplicate_and_store.return_value = DeduplicationResult(
        action="inserted", memory_id="m-1"
    )
    application.state.memory_extractor = MagicMock()
    return application


def _client(app: FastAPI, principal: Principal) -> TestClient:
    """A client whose every request carries `principal`, as the auth middleware sets it."""

    async def with_principal(scope, receive, send):
        scope[PRINCIPAL_SCOPE_KEY] = principal
        await app(scope, receive, send)

    return TestClient(with_principal)


# ---------------------------------------------------------------------------
# SearchService
# ---------------------------------------------------------------------------


def test_search_scopes_tagged_collections_only(search_service, qdrant):
    search_service.search(
        query="q",
        collections=["mnemos_code", "mnemos_knowledge", "mnemos_skills", "mnemos_docs"],
        tags_any=["acme", "other"],
        read_scope=["acme"],
    )
    filters = _filters_by_collection(qdrant)
    for tagged in ("mnemos_code", "mnemos_knowledge"):
        # The caller's own tags_any stays, the read scope is added on top.
        assert _scope_tags(filters[tagged]) == [["acme", "other"], ["acme"]]
    for untagged in ("mnemos_skills", "mnemos_docs"):
        assert _scope_tags(filters[untagged]) == [["acme", "other"]]


def test_search_without_scope_adds_no_condition(search_service, qdrant):
    search_service.search(query="q", collections=["mnemos_code"])
    assert _filters_by_collection(qdrant)["mnemos_code"] is None


def test_search_code_and_memory_apply_the_read_scope(search_service, qdrant):
    search_service.search_code(query="q", tags_all=["go"], read_scope=["acme"])
    assert _scope_tags(_filters_by_collection(qdrant)["mnemos_code"]) == [["go"], ["acme"]]

    search_service.search_memory(query="q", read_scope=["acme"])
    memory_filter = _filters_by_collection(qdrant)["mnemos_memory"]
    assert _scope_tags(memory_filter) == [["acme"]]
    assert "status" in [c.key for c in memory_filter.must]


def test_empty_read_scope_matches_nothing_rather_than_everything(search_service, qdrant):
    search_service.search_code(query="q", read_scope=[])
    assert _scope_tags(_filters_by_collection(qdrant)["mnemos_code"]) == [[]]


def test_skills_search_is_never_scoped(search_service, qdrant):
    search_service.search_skills(query="q")
    assert _filters_by_collection(qdrant)["mnemos_skills"] is None


def test_cache_namespace_differs_per_read_scope(search_service, qdrant):
    cache = MagicMock(enabled=True)
    cache.lookup.return_value = None
    search_service._cache = cache

    for scope in (None, ["acme"], ["other"], [], ["acme", "other"]):
        search_service.search(query="q", collections=["mnemos_code"], read_scope=scope)

    namespaces = [call.kwargs["namespace"] for call in cache.lookup.call_args_list]
    assert len(set(namespaces)) == 5


def test_cache_namespace_ignores_scope_order(search_service, qdrant):
    cache = MagicMock(enabled=True)
    cache.lookup.return_value = None
    search_service._cache = cache

    search_service.search(query="q", read_scope=["b", "a"])
    search_service.search(query="q", read_scope=["a", "b"])

    first, second = (call.kwargs["namespace"] for call in cache.lookup.call_args_list)
    assert first == second


# ---------------------------------------------------------------------------
# Search routes
# ---------------------------------------------------------------------------


def test_search_routes_pass_the_principal_read_scope(app, qdrant):
    qdrant.query_points.return_value = MagicMock(points=[_hit(["acme"])])
    client = _client(app, ACME)

    assert client.post("/api/search", json={"query": "q", "collections": ["mnemos_code"]}).status_code == 200
    assert _scope_tags(_filters_by_collection(qdrant)["mnemos_code"]) == [["acme"]]

    qdrant.query_points.reset_mock()
    assert client.post("/api/search-code", json={"query": "q"}).status_code == 200
    assert _scope_tags(_filters_by_collection(qdrant)["mnemos_code"]) == [["acme"]]

    qdrant.query_points.reset_mock()
    assert client.post("/api/search-memory", json={"query": "q"}).status_code == 200
    assert _scope_tags(_filters_by_collection(qdrant)["mnemos_memory"]) == [["acme"]]


def test_full_access_search_is_unfiltered(app, qdrant):
    _client(app, FULL_ACCESS).post("/api/search-code", json={"query": "q"})
    assert _filters_by_collection(qdrant)["mnemos_code"] is None


def test_search_skills_is_open_to_a_read_only_tenant(app):
    assert _client(app, READ_ONLY).post("/api/search-skills", json={"query": "q"}).status_code == 200


def test_facets_are_restricted_to_the_read_scope(app, qdrant):
    qdrant.facet.return_value = MagicMock(hits=[])
    client = _client(app, ACME)

    assert client.get("/api/index/facets", params={"collection": "mnemos_code", "field": "tags"}).status_code == 200
    assert _scope_tags(qdrant.facet.call_args.kwargs["facet_filter"]) == [["acme"]]

    client.get("/api/index/facets", params={"collection": "mnemos_skills", "field": "language"})
    assert qdrant.facet.call_args.kwargs["facet_filter"] is None


def test_listing_files_of_an_unreadable_tag_is_forbidden(app):
    client = _client(app, ACME)
    assert client.get("/api/index/mnemos_knowledge/files", params={"tag": "other"}).status_code == 403
    assert client.get("/api/index/mnemos_knowledge/files", params={"tag": "acme"}).status_code == 200


# ---------------------------------------------------------------------------
# Push / delete / reindex
# ---------------------------------------------------------------------------


def _push(client, **fields):
    body = {"file_path": "acme/a.go", "collection": "mnemos_code", "content": "package a", **fields}
    return client.post("/api/index", json=body)


def test_push_with_a_foreign_declared_tag_is_forbidden(app):
    response = _push(_client(app, ACME), tags=["acme", "other"])
    assert response.status_code == 403
    app.state.indexer.index_file.assert_not_called()


def test_push_with_declared_tags_inside_the_write_scope_passes(app):
    assert _push(_client(app, ACME), tags=["acme"]).status_code == 200


def test_push_with_resolved_tags_meeting_the_write_scope_passes(app):
    # One shared tag is enough when the server chose the tags.
    app.state.indexer.resolve_tags.return_value = ["acme", "go"]
    assert _push(_client(app, ACME)).status_code == 200
    app.state.indexer.resolve_tags.assert_called_once_with("acme/a.go", None)
    app.state.indexer.index_file.assert_called_once()


def test_push_with_resolved_tags_outside_the_write_scope_is_forbidden(app):
    app.state.indexer.resolve_tags.return_value = ["other"]
    assert _push(_client(app, ACME)).status_code == 403
    app.state.indexer.index_file.assert_not_called()


def test_push_into_an_untagged_collection_needs_write_all(app):
    assert _push(_client(app, ACME), collection="mnemos_skills").status_code == 403
    assert _push(_client(app, FULL_ACCESS), collection="mnemos_skills").status_code == 200


def _stored_chunks(qdrant, *tag_lists):
    qdrant.scroll.return_value = ([SimpleNamespace(payload={"tags": tags}) for tags in tag_lists], None)


def test_delete_of_a_foreign_file_is_forbidden(app, qdrant):
    _stored_chunks(qdrant, ["other"])
    response = _client(app, ACME).delete("/api/index/mnemos_code/other/a.go")
    assert response.status_code == 403
    app.state.indexer.delete_file.assert_not_called()
    scroll_filter = qdrant.scroll.call_args.kwargs["scroll_filter"]
    assert [(c.key, c.match.value) for c in scroll_filter.must] == [("file_path", "other/a.go")]


def test_delete_needs_every_stored_chunk_writable(app, qdrant):
    _stored_chunks(qdrant, ["acme"], ["other"])
    assert _client(app, ACME).delete("/api/index/mnemos_code/acme/a.go").status_code == 403

    _stored_chunks(qdrant, ["acme", "go"], ["acme"])
    assert _client(app, ACME).delete("/api/index/mnemos_code/acme/a.go").status_code == 200
    app.state.indexer.delete_file.assert_called_once_with(file_path="acme/a.go", collection="mnemos_code")


def test_delete_of_an_unknown_file_behaves_as_before(app, qdrant):
    assert _client(app, ACME).delete("/api/index/mnemos_code/acme/gone.go").status_code == 200


def test_reindex_needs_write_all(app):
    client = _client(app, ACME)
    assert client.post("/api/reindex", json={"collection": "mnemos_code"}).status_code == 403
    internal = {"file_path": "/data/codebase/acme/a.go", "collection": "mnemos_code"}
    assert client.post("/internal/reindex", json=internal).status_code == 403
    app.state.indexer.ensure_collection.assert_not_called()


# ---------------------------------------------------------------------------
# Memories
# ---------------------------------------------------------------------------


def test_memory_create_with_a_tag_outside_the_write_scope_is_forbidden(app):
    client = _client(app, ACME)
    assert client.post("/api/memory", json={"content": "c", "tags": ["acme", "other"]}).status_code == 403
    assert client.post("/api/memory", json={"content": "c"}).status_code == 403
    app.state.deduplicator.deduplicate_and_store.assert_not_called()
    assert client.post("/api/memory", json={"content": "c", "tags": ["acme"]}).status_code == 200


def test_memory_review_checks_the_stored_tags(app, qdrant):
    qdrant.scroll.return_value = ([SimpleNamespace(payload={"id": "m", "tags": ["other"]}, vector=[0.1])], None)
    client = _client(app, ACME)
    assert client.post("/api/memory/m/review", json={"action": "approve"}).status_code == 403
    qdrant.upsert.assert_not_called()

    qdrant.scroll.return_value = ([SimpleNamespace(payload={"id": "m", "tags": ["acme"]}, vector=[0.1])], None)
    assert client.post("/api/memory/m/review", json={"action": "approve"}).status_code == 200


def test_memory_list_and_stats_are_read_scoped(app, qdrant):
    client = _client(app, ACME)
    client.get("/api/memory")
    assert _scope_tags(qdrant.scroll.call_args.kwargs["scroll_filter"]) == [["acme"]]
    client.get("/api/memory/stats")
    assert _scope_tags(qdrant.scroll.call_args.kwargs["scroll_filter"]) == [["acme"]]


def test_memory_extract_needs_write_all(app):
    body = {"commit_message": "m", "diff": "d"}
    assert _client(app, ACME).post("/api/memory/extract", json=body).status_code == 403
    app.state.memory_extractor.extract.assert_not_called()


# ---------------------------------------------------------------------------
# Admin-only observability
# ---------------------------------------------------------------------------


def test_query_log_and_eval_are_admin_only(app):
    client = _client(app, ACME)
    assert client.get("/api/query-log").status_code == 403
    assert client.get("/api/eval/runs").status_code == 403
    assert client.post("/api/eval/sample", json={"collection": "mnemos_code"}).status_code == 403


def test_status_is_open_to_every_principal(app, qdrant):
    qdrant.collection_exists.return_value = False
    assert _client(app, READ_ONLY).get("/api/status").status_code == 200


# ---------------------------------------------------------------------------
# MCP
# ---------------------------------------------------------------------------


def _dispatch(principal, name, args, search_service, qdrant=None, deduplicator=None):
    return asyncio.run(_dispatch_tool(
        name=name,
        args=args,
        principal=principal,
        search_service=search_service,
        indexer=MagicMock(),
        qdrant_client=qdrant,
        deduplicator=deduplicator,
    ))


def test_mcp_searches_filter_like_the_routes(search_service, qdrant):
    _dispatch(ACME, "mnemos_search", {"query": "q", "collections": ["mnemos_code", "mnemos_skills"]}, search_service)
    filters = _filters_by_collection(qdrant)
    assert _scope_tags(filters["mnemos_code"]) == [["acme"]]
    assert filters["mnemos_skills"] is None

    _dispatch(ACME, "mnemos_search_code", {"query": "q"}, search_service)
    assert _scope_tags(_filters_by_collection(qdrant)["mnemos_code"]) == [["acme"]]

    _dispatch(ACME, "mnemos_search_memory", {"query": "q"}, search_service)
    assert _scope_tags(_filters_by_collection(qdrant)["mnemos_memory"]) == [["acme"]]


def test_mcp_memory_list_is_read_scoped(search_service, qdrant):
    _dispatch(ACME, "mnemos_memory_list", {}, search_service, qdrant=qdrant)
    assert _scope_tags(qdrant.scroll.call_args.kwargs["scroll_filter"]) == [["acme"]]


def test_mcp_writes_respect_the_write_scope(search_service, qdrant):
    deduplicator = MagicMock()
    with pytest.raises(Exception, match="403"):
        _dispatch(ACME, "mnemos_memory", {"content": "c", "tags": ["other"]}, search_service,
                  deduplicator=deduplicator)
    deduplicator.deduplicate_and_store.assert_not_called()

    with pytest.raises(Exception, match="403"):
        _dispatch(ACME, "mnemos_reindex", {}, search_service)

    qdrant.retrieve.return_value = [SimpleNamespace(payload={"tags": ["other"]})]
    with pytest.raises(Exception, match="403"):
        _dispatch(ACME, "mnemos_memory_review", {"memory_id": "m", "action": "approve"},
                  search_service, qdrant=qdrant)
    qdrant.set_payload.assert_not_called()


def _call_tool(mcp_server, name: str, args: dict, request=None) -> str:
    handler = mcp_server.request_handlers[types.CallToolRequest]
    req = types.CallToolRequest(
        method="tools/call", params=types.CallToolRequestParams(name=name, arguments=args)
    )

    async def run():
        if request is not None:
            request_ctx.set(RequestContext(
                request_id=1, meta=None, session=MagicMock(), lifespan_context=None, request=request
            ))
        return await handler(req)

    return asyncio.run(run()).root.content[0].text


def test_mcp_call_reads_the_principal_from_the_http_request():
    search_service = MagicMock()
    search_service.search_code.return_value = []
    mcp_server = create_mcp_server(search_service=search_service, indexer=MagicMock())
    request = SimpleNamespace(scope={PRINCIPAL_SCOPE_KEY: ACME})

    _call_tool(mcp_server, "mnemos_search_code", {"query": "q"}, request=request)
    assert search_service.search_code.call_args.kwargs["read_scope"] == ["acme"]

    text = _call_tool(mcp_server, "mnemos_reindex", {}, request=request)
    assert text.startswith("Error: 403")


def test_mcp_call_without_credentials_fails_closed_when_auth_is_on(monkeypatch):
    monkeypatch.setattr(settings, "mnemos_auth_enabled", True)
    search_service = MagicMock()
    mcp_server = create_mcp_server(search_service=search_service, indexer=MagicMock())

    text = _call_tool(mcp_server, "mnemos_search_code", {"query": "q"})
    assert text.startswith("Error: 401")
    search_service.search_code.assert_not_called()
