"""Sub-application APIs, shared DB access, app file serving, and Hub sync."""

from __future__ import annotations

import sqlite3
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel

from core.modes.apps.app_packager import AppPackager
from core.assets.tools import normalize_for_app_tool_proxy
from core.systems.runtime import safe_resolve
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


class AppPublishRequest(BaseModel):
    version: str = "0.1.0"
    changelog: str = ""
    hub_url: str = ""
    hub_token: str = ""


class AppInstallRequest(BaseModel):
    slug: str
    version: str = "latest"
    overwrite: bool = False
    hub_url: str = ""
    hub_token: str = ""


class AppImportRequest(BaseModel):
    bundle: dict[str, Any]
    overwrite: bool = False
    target_name: str = ""


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


class AppModeSwitchRequest(BaseModel):
    mode: str
    rebuild_template: bool = False

@router.patch("/api/apps/{app_name}/mode")
async def switch_app_mode(
    app_name: str,
    req: AppModeSwitchRequest,
    services: WebServices = Depends(get_services),
) -> dict[str, object]:
    result = services.app_manager.switch_app_mode(app_name, req.mode, req.rebuild_template)
    if not result.get("success"):
        raise HTTPException(status_code=400, detail=result.get("error"))
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


@router.post("/api/apps/{app_name}/tool/{tool_name}/run")
async def call_app_tool(
    app_name: str,
    tool_name: str,
    payload: dict[str, Any],
    services: WebServices = Depends(get_services),
) -> Any:
    app_def = services.app_manager.get_app(app_name)
    if not app_def:
        raise HTTPException(status_code=404, detail="App not found")
    if not app_def.enabled:
        raise HTTPException(status_code=403, detail="App is disabled")
    
    # Check if tool is allowed for this app (if allowed_tools is specified)
    if app_def.allowed_tools and tool_name not in app_def.allowed_tools:
        raise HTTPException(status_code=403, detail=f"Tool '{tool_name}' not allowed for this app")
        
    # Get tool from the global tool storage via the runtime
    try:
        # Get the default agent to access the global storage
        agent = services.agents.get_or_create_mode("assistant", "default")
        
        tool = None
        
        # Try middleware
        if not tool and hasattr(agent, "middleware") and hasattr(agent.middleware, "get_all_tools"):
            tools = agent.middleware.get_all_tools()
            tool = next((t for t in tools if t.name == tool_name), None)
            
        # Fallback to storage
        if not tool and hasattr(agent, "storage"):
            tool = agent.storage.get_tool(tool_name)
            
        # Fallback to skill registry
        if not tool and hasattr(agent, "skill_registry"):
            skill_tools = agent.skill_registry.get_active_tools()
            tool = next((t for t in skill_tools if t.name == tool_name), None)
            
        # Fallback to capability bus
        if not tool and hasattr(agent, "capability_bus"):
            # capability_bus returns registered tools via its internal storage or dynamically
            if hasattr(agent.capability_bus, "get_tools"):
                bus_tools = agent.capability_bus.get_tools()
                tool = next((t for t in bus_tools if t.name == tool_name), None)
            
        # Fallback to direct tool instances if any
        if not tool and hasattr(agent, "tools"):
            tool = next((t for t in agent.tools if t.name == tool_name), None)
            
    except Exception as e:
        import logging
        logging.getLogger(__name__).error(f"Failed to get tool {tool_name} from storage: {e}")
        tool = None
        
    if not tool:
        import logging
        logging.getLogger(__name__).error(f"Tool {tool_name} not found.")
        raise HTTPException(status_code=404, detail=f"Tool '{tool_name}' not found")
        
    try:
        # Handle different tool invocation signatures
        if hasattr(tool, "invoke"):
            result = tool.invoke(payload)
        elif hasattr(tool, "_run"):
            result = tool._run(**payload)
        elif hasattr(tool, "run"):
            result = tool.run(**payload)
        else:
            result = tool(**payload)
            
        return normalize_for_app_tool_proxy(result)
    except Exception as exc:
        import logging
        logging.getLogger(__name__).error(f"Error executing tool {tool_name}: {exc}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(exc))


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
    headers = {}
    if full_path.suffix in (".js", ".css", ".html"):
        headers["Cache-Control"] = "no-cache, must-revalidate"
    return FileResponse(str(full_path), headers=headers)


@router.get("/apps/{app_name}/")
async def serve_app_index(
    app_name: str,
    services: WebServices = Depends(get_services),
) -> FileResponse:
    return await serve_app_file(app_name, "", services)


def _get_packager(services: WebServices) -> AppPackager:
    return AppPackager(services.app_manager.apps_dir)


def _get_hub_client(hub_url: str, hub_token: str) -> Any:
    from core.systems.integration.pyhub_client import PyHubClient

    url = hub_url or "http://localhost:8000"
    return PyHubClient(registry_url=url, api_key=hub_token or None)


@router.get("/api/apps/{app_name}/bundle")
async def export_app_bundle(
    app_name: str,
    services: WebServices = Depends(get_services),
) -> dict[str, object]:
    packager = _get_packager(services)
    result = packager.export_bundle(app_name)
    if not result.get("success"):
        raise HTTPException(status_code=404, detail=result.get("error"))
    return result


@router.get("/api/apps/{app_name}/download")
async def download_app_zip(
    app_name: str,
    services: WebServices = Depends(get_services),
) -> Response:
    packager = _get_packager(services)
    zip_data = packager.export_zip(app_name)
    if zip_data is None:
        raise HTTPException(status_code=404, detail="App not found")
    return Response(
        content=zip_data,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{app_name}.zip"'},
    )


@router.get("/api/apps/{app_name}/dependencies")
async def get_app_dependencies(
    app_name: str,
    services: WebServices = Depends(get_services),
) -> dict[str, object]:
    packager = _get_packager(services)
    result = packager.get_dependency_info(app_name)
    if not result.get("success"):
        raise HTTPException(status_code=404, detail=result.get("error"))
    return result


@router.post("/api/apps/{app_name}/publish")
async def publish_app_to_hub(
    app_name: str,
    req: AppPublishRequest,
    services: WebServices = Depends(get_services),
) -> dict[str, object]:
    packager = _get_packager(services)
    hub_client = _get_hub_client(req.hub_url, req.hub_token)
    result = packager.publish_to_hub(
        app_name,
        hub_client,
        version=req.version,
        changelog=req.changelog,
    )
    if not result.get("success"):
        raise HTTPException(status_code=400, detail=result.get("error"))
    return result


@router.post("/api/apps/install-from-hub")
async def install_app_from_hub(
    req: AppInstallRequest,
    services: WebServices = Depends(get_services),
) -> dict[str, object]:
    packager = _get_packager(services)
    hub_client = _get_hub_client(req.hub_url, req.hub_token)
    result = packager.install_from_hub(
        req.slug,
        hub_client,
        version=req.version,
        overwrite=req.overwrite,
    )
    if not result.get("success"):
        raise HTTPException(status_code=400, detail=result.get("error"))
    services.app_manager.reload_apps()
    return result


@router.post("/api/apps/import")
async def import_app_bundle(
    req: AppImportRequest,
    services: WebServices = Depends(get_services),
) -> dict[str, object]:
    packager = _get_packager(services)
    result = packager.import_bundle(
        req.bundle,
        overwrite=req.overwrite,
        target_name=req.target_name or None,
    )
    if not result.get("success"):
        raise HTTPException(status_code=400, detail=result.get("error"))
    services.app_manager.reload_apps()
    return result
