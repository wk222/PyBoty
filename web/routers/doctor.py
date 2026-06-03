"""System doctor API — OpenClaw-style health checks for team deployments."""

from __future__ import annotations

from fastapi import APIRouter, Depends

from core.systems.runtime.system_doctor import bootstrap_team_workspace, run_system_doctor
from web.dependencies import get_services
from web.state import WebServices

router = APIRouter(tags=["doctor"])


@router.get("/api/doctor")
async def get_doctor_report(services: WebServices = Depends(get_services)) -> dict[str, object]:
    report = run_system_doctor(workspace_dir=services.paths.workspace_dir)
    payload = report.to_dict()
    payload["workspace_dir"] = str(services.paths.workspace_dir)
    return payload


@router.post("/api/doctor/bootstrap")
async def bootstrap_workspace(services: WebServices = Depends(get_services)) -> dict[str, object]:
    result = bootstrap_team_workspace(services.paths.workspace_dir)
    report = run_system_doctor(workspace_dir=services.paths.workspace_dir)
    return {
        **result,
        "doctor": report.to_dict(),
    }


@router.post("/api/openclaw/import-channels")
async def import_openclaw_channels_only(services: WebServices = Depends(get_services)) -> dict[str, object]:
    """Import supported channel blocks from ~/.openclaw/openclaw.json into PyBot config."""
    from core.assets.skills.openclaw_compat import import_openclaw_channels_for_pybot, try_load_openclaw_config
    from core.systems.runtime import get_config, save_config

    _path, openclaw_config, error = try_load_openclaw_config()
    if openclaw_config is None:
        from fastapi import HTTPException

        raise HTTPException(status_code=400, detail=error or "OpenClaw config not found")

    current = get_config()
    result = import_openclaw_channels_for_pybot(openclaw_config, current.get("channels"))
    current["channels"] = result["channels"]
    save_config(current)
    return result
