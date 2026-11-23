"""Who is calling, and which tags they may read and write.

`BearerAuthMiddleware` resolves the bearer key to a `Principal` and stores it
in the ASGI scope under `PRINCIPAL_SCOPE_KEY`; routes read it with
`principal_of(request)`, MCP tools with `principal_from_scope(request.scope)`.

Scopes are tags. A chunk, memory or page is readable when its tags meet the
read scope (one shared tag is enough: a parent tag such as `acme` covers every
chunk tagged with it). Writes differ by who chose the tags:

- resolved by the server (code pushed for indexing, tags from `projects`):
  one tag in the write scope is enough, as for reads;
- declared by the client (memories, connector pages, an explicit `tags`
  override): every tag must be in the write scope, or a scoped tenant could
  push content into another project's view.

`None` means every tag. Collections without tags (skills, docs) are readable
by every principal and writable only with `write_all`.
"""
from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any

from fastapi import HTTPException, Request

from server.config import settings

PRINCIPAL_SCOPE_KEY = "mnemos.principal"


@dataclass(frozen=True)
class Principal:
    name: str
    admin: bool
    read: frozenset[str] | None   # None: every tag
    write: frozenset[str] | None  # None: every tag; empty: read-only

    @property
    def read_all(self) -> bool:
        return self.read is None

    @property
    def write_all(self) -> bool:
        return self.write is None

    def read_filter(self) -> list[str] | None:
        """Tags a query must be restricted to (match any), or None for no restriction."""
        return None if self.read is None else sorted(self.read)

    def can_read(self, tags: Iterable[str]) -> bool:
        return self.read is None or not self.read.isdisjoint(tags)

    def can_write_resolved(self, tags: Iterable[str]) -> bool:
        return self.write is None or not self.write.isdisjoint(tags)

    def can_write_declared(self, tags: Iterable[str]) -> bool:
        if self.write is None:
            return True
        tags = set(tags)
        return bool(tags) and tags <= self.write


# Auth off: every caller is trusted, as before scopes existed.
FULL_ACCESS = Principal(name="anonymous", admin=True, read=None, write=None)


def principal_from_scope(scope: Mapping[str, Any]) -> Principal:
    principal = scope.get(PRINCIPAL_SCOPE_KEY)
    if principal is not None:
        return principal
    if settings.mnemos_auth_enabled:
        # A protected path always carries one; its absence is a wiring bug,
        # and failing open here would hand out full access.
        raise HTTPException(status_code=401, detail="Missing or invalid API key")
    return FULL_ACCESS


def principal_of(request: Request) -> Principal:
    return principal_from_scope(request.scope)


def forbidden(detail: str) -> HTTPException:
    return HTTPException(status_code=403, detail=detail)


def require_admin(principal: Principal) -> None:
    if not principal.admin:
        raise forbidden(f"Tenant {principal.name!r} is not an admin")


def require_write_all(principal: Principal) -> None:
    if not principal.write_all:
        raise forbidden(f"Tenant {principal.name!r} cannot write outside its projects")


def require_admin_or_write_all(principal: Principal) -> None:
    if not (principal.admin or principal.write_all):
        raise forbidden(f"Tenant {principal.name!r} needs admin rights or write access to every tag")
