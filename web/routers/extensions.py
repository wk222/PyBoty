"""Web APIs for installed extensions: list, schema, settings, marketplace."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import APIRouter, Body, HTTPException, Query

from core.plugin_sdk.installer import list_installed
from core.plugin_sdk.marketplace import MarketplaceError, MarketplaceIndex
from core.plugin_sdk.settings import SettingsError, SettingsStore

router = APIRouter(tags=["extensions"])

_DEFAULT_EXTENSIONS_ROOT = Path("workspace/extensions")
_DEFAULT_MARKETPLACE = Path("workspace/marketplace.json")


def _store() -> SettingsStore:
    return SettingsStore(_DEFAULT_EXTENSIONS_ROOT)


@router.get("/api/extensions")
async def list_extensions() -> dict[str, Any]:
    manifests = list_installed(_DEFAULT_EXTENSIONS_ROOT)
    return {
        "count": len(manifests),
        "extensions": [m.to_dict() for m in manifests],
    }


@router.get("/api/extensions/{extension_id}")
async def get_extension(extension_id: str) -> dict[str, Any]:
    manifests = list_installed(_DEFAULT_EXTENSIONS_ROOT)
    for m in manifests:
        if m.id == extension_id:
            return m.to_dict()
    raise HTTPException(status_code=404, detail="extension not found")


@router.get("/api/extensions/{extension_id}/schema")
async def get_extension_schema(extension_id: str) -> dict[str, Any]:
    schema = _store().schema_for(extension_id)
    if schema is None:
        raise HTTPException(status_code=404, detail="schema not found")
    return schema.to_dict()


@router.get("/api/extensions/{extension_id}/settings")
async def get_extension_settings(extension_id: str) -> dict[str, Any]:
    try:
        return _store().read(extension_id)
    except SettingsError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.put("/api/extensions/{extension_id}/settings")
async def put_extension_settings(
    extension_id: str,
    settings: dict[str, Any] = Body(default_factory=dict),
) -> dict[str, Any]:
    try:
        return _store().write(extension_id, settings)
    except SettingsError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/api/marketplace")
async def search_marketplace(
    q: str = Query(default=""),
    kind: str | None = Query(default=None),
    tag: str | None = Query(default=None),
) -> dict[str, Any]:
    if not _DEFAULT_MARKETPLACE.exists():
        return {"count": 0, "entries": [], "available": False}
    try:
        index = MarketplaceIndex.from_path(_DEFAULT_MARKETPLACE)
    except MarketplaceError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    matches = index.search(q, kind=kind, tag=tag)
    return {
        "count": len(matches),
        "available": True,
        "entries": [m.to_dict() for m in matches],
    }
