"""Sub-application APIs, shared DB access, and app file serving."""

from __future__ import annotations

import sqlite3
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel

from core.path_utils import safe_resolve
from web.dependencies import get_services
from web.state import WebServices

router = APIRouter(tags=["apps"])


class AppToggleRequest(BaseModel):
    enabled: bool


class AppApiRequest(BaseModel):
    action: str
    payload: dict[str, Any] = {}


class DbQueryRequest(BaseModel):
    sql: str


class DbWriteRequest(BaseModel):
    sql: str
    params: list[Any] = []


@router.get("/api/apps")
async def list_apps(services: WebServices = Depends(get_services)) -> dict[str, object]:
    services.app_manager.reload_apps()
    apps = services.app_manager.list_apps()
    for app in apps:
        app["url"] = f"/apps/{app['name']}/"
    return {"apps": apps, "count": len(apps)}


@router.get("/api/apps/{app_name}/info")
async def get_app_info(
    app_name: str,
    services: WebServices = Depends(get_services),
) -> dict[str, object]:
    app_def = services.app_manager.get_app(app_name)
    if not app_def:
        raise HTTPException(status_code=404, detail="App not found")
    data = app_def.to_dict()
    data["url"] = f"/apps/{app_name}/"
    files = services.app_manager.list_app_files(app_name)
    data["files"] = files.get("files", [])
    return data


@router.delete("/api/apps/{app_name}")
async def delete_app(
    app_name: str,
    services: WebServices = Depends(get_services),
) -> dict[str, object]:
    result = services.app_manager.delete_app(app_name)
    if not result.get("success"):
        raise HTTPException(status_code=404, detail=result.get("error"))
    return result


@router.patch("/api/apps/{app_name}/toggle")
async def toggle_app(
    app_name: str,
    req: AppToggleRequest,
    services: WebServices = Depends(get_services),
) -> dict[str, object]:
    result = services.app_manager.toggle_app(app_name, req.enabled)
    if not result.get("success"):
        raise HTTPException(status_code=404, detail=result.get("error"))
    return result


@router.post("/api/apps/{app_name}/api")
async def call_app_api(
    app_name: str,
    req: AppApiRequest,
    services: WebServices = Depends(get_services),
) -> dict[str, object]:
    app_def = services.app_manager.get_app(app_name)
    if app_def and not app_def.enabled:
        raise HTTPException(status_code=403, detail="App is disabled")
    return services.app_manager.execute_app_api(app_name, req.action, req.payload)


@router.post("/api/apps/~db/query")
async def app_db_query(
    req: DbQueryRequest,
    services: WebServices = Depends(get_services),
) -> dict[str, object]:
    sql_lower = req.sql.lower().strip()
    if not (sql_lower.startswith("select") or sql_lower.startswith("with")):
        raise HTTPException(status_code=400, detail="Only SELECT/WITH queries allowed")

    db_path = services.paths.workspace_data_dir / "agent.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)

    try:
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute(req.sql)
        rows = [dict(row) for row in cursor.fetchall()]
        columns = [desc[0] for desc in cursor.description] if cursor.description else []
        conn.close()
        return {"success": True, "data": rows, "columns": columns, "row_count": len(rows)}
    except Exception as exc:
        return {"success": False, "error": str(exc)}


@router.post("/api/apps/~db/write")
async def app_db_write(
    req: DbWriteRequest,
    services: WebServices = Depends(get_services),
) -> dict[str, object]:
    db_path = services.paths.workspace_data_dir / "agent.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)

    try:
        conn = sqlite3.connect(db_path)
        conn.execute("PRAGMA journal_mode=WAL")
        cursor = conn.cursor()
        cursor.execute(req.sql, req.params or [])
        conn.commit()
        affected_rows = cursor.rowcount
        conn.close()
        return {"success": True, "affected_rows": affected_rows}
    except Exception as exc:
        return {"success": False, "error": str(exc)}


@router.get("/apps/{app_name}/{file_path:path}")
async def serve_app_file(
    app_name: str,
    file_path: str = "",
    services: WebServices = Depends(get_services),
) -> FileResponse:
    app_def = services.app_manager.get_app(app_name)
    if not app_def:
        raise HTTPException(status_code=404, detail="App not found")
    if not app_def.enabled:
        raise HTTPException(status_code=403, detail="App is disabled")

    app_dir = services.app_manager.get_app_dir(app_name)
    requested_path = app_def.entry_point if not file_path or file_path == "/" else file_path
    try:
        full_path = safe_resolve(app_dir, requested_path)
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail="Access denied") from exc
    if not full_path.exists():
        raise HTTPException(status_code=404, detail=f"File not found: {requested_path}")
    return FileResponse(str(full_path))


@router.get("/apps/{app_name}/")
async def serve_app_index(
    app_name: str,
    services: WebServices = Depends(get_services),
) -> FileResponse:
    return await serve_app_file(app_name, "", services)
