"""REST surface of the dashboard's configuration page: /api/config."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from server.settings_store import ConfigStore, ConfigValidationError

config_router = APIRouter()

_READ_ONLY = (
    "Configuration is read-only: set MNEMOS_UI_CONFIG_WRITABLE=true on the server and restart it"
)


class ConfigPatchRequest(BaseModel):
    values: dict[str, Any]


def _writable_store(request: Request) -> ConfigStore:
    store: ConfigStore = request.app.state.config_store
    if not store.writable:
        raise HTTPException(status_code=403, detail=_READ_ONLY)
    return store


def _persist_failed(store: ConfigStore, exc: OSError) -> HTTPException:
    return HTTPException(
        status_code=500,
        detail=f"Could not write {store.path}: {exc.strerror or exc}",
    )


@config_router.get("/api/config")
def get_config(request: Request):
    return request.app.state.config_store.snapshot()


@config_router.patch("/api/config")
def patch_config(body: ConfigPatchRequest, request: Request):
    store = _writable_store(request)
    try:
        applied = store.update(body.values, request.app.state)
    except ConfigValidationError as exc:
        return JSONResponse(
            status_code=422,
            content={"detail": "Invalid settings, nothing was saved", "errors": exc.errors},
        )
    except OSError as exc:
        raise _persist_failed(store, exc) from exc
    return {**store.snapshot(), "applied": applied}


@config_router.delete("/api/config/{key}")
def delete_config_override(key: str, request: Request):
    store = _writable_store(request)
    try:
        store.reset(key, request.app.state)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"No override for {key!r}") from None
    except OSError as exc:
        raise _persist_failed(store, exc) from exc
    return store.snapshot()
