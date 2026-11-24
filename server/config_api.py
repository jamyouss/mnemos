"""REST surface of the dashboard's configuration page: /api/config.

Also the helpers every configuration route shares: the write switch and the
409 body of a stale revision.
"""
from __future__ import annotations

from typing import Any

from core.config_store import RevisionConflict
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from server.audit import MASK, audit
from server.config import settings
from server.principal import principal_of, require_admin
from server.settings_registry import FIELDS_BY_KEY
from server.settings_store import ConfigValidationError

config_router = APIRouter()

_READ_ONLY = (
    "Configuration is read-only: set MNEMOS_UI_CONFIG_WRITABLE=true on the server and restart it"
)


class ConfigPatchRequest(BaseModel):
    values: dict[str, Any]
    revision: int | None = None


def require_writable() -> None:
    if not settings.mnemos_ui_config_writable:
        raise HTTPException(status_code=403, detail=_READ_ONLY)


def require_admin_writer(request: Request) -> None:
    require_admin(principal_of(request))
    require_writable()


def revision_conflict(exc: RevisionConflict) -> JSONResponse:
    return JSONResponse(status_code=409, content={"detail": str(exc), "revision": exc.current})


@config_router.get("/api/config")
def get_config(request: Request):
    require_admin(principal_of(request))
    return request.app.state.config_store.snapshot()


@config_router.patch("/api/config")
def patch_config(body: ConfigPatchRequest, request: Request):
    require_admin_writer(request)
    store = request.app.state.config_store
    before = {key: getattr(settings, key, None) for key in body.values}
    try:
        applied = store.update(body.values, request.app.state, revision=body.revision)
    except ConfigValidationError as exc:
        return JSONResponse(
            status_code=422,
            content={"detail": "Invalid settings, nothing was saved", "errors": exc.errors},
        )
    except RevisionConflict as exc:
        return revision_conflict(exc)
    audit(request, "config.update", ", ".join(body.values),
          {key: _shown(key, [before[key], value]) for key, value in body.values.items()})
    return {**store.snapshot(), "applied": applied}


def _shown(key: str, pair: list) -> list:
    """A secret setting's values never reach the audit trail."""
    spec = FIELDS_BY_KEY.get(key)
    return [MASK if v not in (None, "") else v for v in pair] if spec and spec.type == "secret" else pair


@config_router.delete("/api/config/{key}")
def delete_config_override(key: str, request: Request, revision: int | None = None):
    require_admin_writer(request)
    store = request.app.state.config_store
    before = getattr(settings, key, None)
    try:
        store.reset(key, request.app.state, revision=revision)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"No override for {key!r}") from None
    except RevisionConflict as exc:
        return revision_conflict(exc)
    audit(request, "config.reset", key, {key: _shown(key, [before, "default"])})
    return store.snapshot()
