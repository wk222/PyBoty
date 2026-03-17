"""Workspace, memory, skills, scheduler, and environment APIs."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from pydantic import BaseModel

from core.task_scheduler import ScheduledTask
from core.tool_templates import get_templates_by_category, list_templates
from web.dependencies import get_services
from web.state import WebServices

router = APIRouter(tags=["workspace"])

ALLOWED_WORKSPACE_FILES = {"SOUL.md", "IDENTITY.md", "MEMORY.md", "SCHEDULE.md"}


class WorkspaceFileUpdate(BaseModel):
    content: str


class MemoryEntry(BaseModel):
    section: str
    content: str


class SkillToggleRequest(BaseModel):
    enabled: bool


class SkillCopyRequest(BaseModel):
    target_source: str
    target_name: str | None = None
    overwrite: bool = False


class SkillImportRequest(BaseModel):
    name: str
    files: dict[str, str]
    overwrite: bool = False


class ScheduleToggleRequest(BaseModel):
    enabled: bool


class ScheduledTaskCreate(BaseModel):
    name: str
    description: str
    cron: str
    prompt: str
    enabled: bool = False


class UvEnvCreateRequest(BaseModel):
    name: str
    description: str = ""
    python_version: str = ""
    tags: list[str] = []


class UvEnvUpdateRequest(BaseModel):
    description: str | None = None
    tags: list[str] | None = None


class UvPackageRequest(BaseModel):
    packages: list[str]


class UvRunCodeRequest(BaseModel):
    code: str
    timeout: int = 60


class UvRunFileRequest(BaseModel):
    filepath: str
    timeout: int = 60


class SkillFileUpdateRequest(BaseModel):
    content: str


@router.get("/api/workspace/files")
async def list_workspace_files(services: WebServices = Depends(get_services)) -> dict[str, object]:
    return {"files": services.workspace_mgr.list_files()}


@router.get("/api/workspace/{filename}")
async def get_workspace_file(
    filename: str,
    services: WebServices = Depends(get_services),
) -> dict[str, str]:
    if filename not in ALLOWED_WORKSPACE_FILES:
        raise HTTPException(status_code=403, detail="Access denied")
    filepath = services.paths.workspace_dir / filename
    if not filepath.exists():
        raise HTTPException(status_code=404, detail="File not found")
    return {"filename": filename, "content": services.workspace_mgr.load_file(filename)}


@router.put("/api/workspace/{filename}")
async def update_workspace_file(
    filename: str,
    req: WorkspaceFileUpdate,
    services: WebServices = Depends(get_services),
) -> dict[str, object]:
    if filename not in ALLOWED_WORKSPACE_FILES:
        raise HTTPException(status_code=403, detail="Access denied")
    ok = services.workspace_mgr.save_file(filename, req.content)
    if not ok:
        raise HTTPException(status_code=500, detail="Failed to save file")
    return {"success": True, "filename": filename}


@router.get("/api/memory")
async def get_memory(services: WebServices = Depends(get_services)) -> dict[str, str]:
    return {"content": services.memory_mgr.load()}


@router.post("/api/memory")
async def add_memory(
    entry: MemoryEntry,
    services: WebServices = Depends(get_services),
) -> dict[str, bool]:
    services.memory_mgr.append_memory(entry.section, entry.content)
    return {"success": True}


@router.get("/api/skills")
async def list_skills(services: WebServices = Depends(get_services)) -> dict[str, object]:
    return {"skills": services.skill_registry.list_skills()}


@router.post("/api/skill-sources/refresh")
async def refresh_skill_sources(services: WebServices = Depends(get_services)) -> dict[str, object]:
    sources = await services.skill_registry.arefresh_sources()
    return {
        "success": True,
        "sources": sources,
        "skills": services.skill_registry.list_skills(),
    }


@router.post("/api/skill-sources/{source_name}/refresh")
async def refresh_skill_source(
    source_name: str,
    services: WebServices = Depends(get_services),
) -> dict[str, object]:
    try:
        source = await services.skill_registry.arefresh_source(source_name)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Skill source not found") from exc
    return {
        "success": True,
        "source": source,
        "skills": services.skill_registry.list_skills(),
    }


@router.get("/api/skill-sources")
async def list_skill_sources(services: WebServices = Depends(get_services)) -> dict[str, object]:
    return {"sources": await services.skill_registry.alist_sources()}


@router.get("/api/skill-sources/{source_name}")
async def get_skill_source(
    source_name: str,
    services: WebServices = Depends(get_services),
) -> dict[str, object]:
    try:
        source = await services.skill_registry.aget_source(source_name)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Skill source not found") from exc
    return {"source": source}


@router.post("/api/skill-sources/{source_name}/skills")
async def import_skill_to_source(
    source_name: str,
    req: SkillImportRequest,
    services: WebServices = Depends(get_services),
) -> dict[str, object]:
    try:
        result = await services.skill_registry.aimport_skill_bundle(
            req.name,
            req.files,
            target_source_name=source_name,
            overwrite=req.overwrite,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Skill source not found") from exc
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail="Skill source is read-only") from exc
    except FileExistsError as exc:
        raise HTTPException(status_code=409, detail=f"Skill already exists: {exc}") from exc
    return {"success": True, "result": result}


@router.get("/api/skills/{skill_name}")
async def get_skill(
    skill_name: str,
    services: WebServices = Depends(get_services),
) -> dict[str, object]:
    skill = services.skill_registry.get_skill(skill_name)
    if not skill:
        raise HTTPException(status_code=404, detail="Skill not found")
    data = skill.to_dict()
    try:
        data["registered_tools"] = [
            tool.name for tool in services.skill_registry.get_active_tools() if hasattr(tool, "_run")
        ]
    except Exception:
        data["registered_tools"] = []
    return data


@router.get("/api/skills/{skill_name}/bundle")
async def export_skill_bundle(
    skill_name: str,
    services: WebServices = Depends(get_services),
) -> dict[str, object]:
    files = await services.skill_registry.aexport_skill_bundle(skill_name)
    if files is None:
        raise HTTPException(status_code=404, detail="Skill not found")
    return {"skill": skill_name, "files": files}


@router.post("/api/skills/{skill_name}/copy")
async def copy_skill_to_source(
    skill_name: str,
    req: SkillCopyRequest,
    services: WebServices = Depends(get_services),
) -> dict[str, object]:
    try:
        result = await services.skill_registry.acopy_skill_to_source(
            skill_name,
            target_source_name=req.target_source,
            target_name=req.target_name,
            overwrite=req.overwrite,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Skill source not found") from exc
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail="Skill source is read-only") from exc
    except FileExistsError as exc:
        raise HTTPException(status_code=409, detail=f"Skill already exists: {exc}") from exc
    if result is None:
        raise HTTPException(status_code=404, detail="Skill not found")
    return {"success": True, "result": result}


@router.patch("/api/skills/{skill_name}/toggle")
async def toggle_skill(
    skill_name: str,
    req: SkillToggleRequest,
    services: WebServices = Depends(get_services),
) -> dict[str, object]:
    skill = services.skill_registry.get_skill(skill_name)
    if skill is None:
        raise HTTPException(status_code=404, detail="Skill not found")
    if not skill.writable:
        raise HTTPException(status_code=403, detail="Skill source is read-only")
    ok = await services.skill_registry.atoggle_skill(skill_name, req.enabled)
    if not ok:
        raise HTTPException(status_code=500, detail="Failed to toggle skill")
    return {"success": True, "skill_name": skill_name, "enabled": req.enabled}


@router.delete("/api/skills/{skill_name}")
async def delete_skill(
    skill_name: str,
    services: WebServices = Depends(get_services),
) -> dict[str, object]:
    skill = services.skill_registry.get_skill(skill_name)
    if skill is None:
        raise HTTPException(status_code=404, detail="Skill not found")
    if not skill.writable:
        raise HTTPException(status_code=403, detail="Skill source is read-only")
    ok = services.skill_registry.remove_skill(skill_name)
    if not ok:
        raise HTTPException(status_code=500, detail="Failed to delete skill")
    return {"success": True, "deleted": skill_name}


@router.get("/api/skills/{skill_name}/files")
async def list_skill_files(
    skill_name: str,
    services: WebServices = Depends(get_services),
) -> dict[str, object]:
    files = await services.skill_registry.alist_skill_files(skill_name)
    if files is None:
        raise HTTPException(status_code=404, detail="Skill not found")
    return {"skill": skill_name, "files": files}


@router.get("/api/skills/{skill_name}/files/{file_path:path}")
async def get_skill_file(
    skill_name: str,
    file_path: str,
    services: WebServices = Depends(get_services),
) -> dict[str, str]:
    try:
        content = await services.skill_registry.aread_skill_file(skill_name, file_path)
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail="Access denied") from exc
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="File not found") from exc
    if content is None:
        raise HTTPException(status_code=404, detail="Skill not found")
    return {"path": file_path, "content": content}


@router.put("/api/skills/{skill_name}/files/{file_path:path}")
async def update_skill_file(
    skill_name: str,
    file_path: str,
    req: SkillFileUpdateRequest,
    services: WebServices = Depends(get_services),
) -> dict[str, object]:
    skill = services.skill_registry.get_skill(skill_name)
    if skill is None:
        raise HTTPException(status_code=404, detail="Skill not found")
    if not skill.writable:
        raise HTTPException(status_code=403, detail="Skill source is read-only")
    try:
        ok = await services.skill_registry.awrite_skill_file(skill_name, file_path, req.content)
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail="Access denied") from exc
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="File not found") from exc
    if not ok:
        raise HTTPException(status_code=500, detail="Failed to update file")
    return {"success": True, "path": file_path}


@router.get("/api/schedule/tasks")
async def list_schedule_tasks(services: WebServices = Depends(get_services)) -> dict[str, object]:
    return {"tasks": services.task_scheduler.list_tasks()}


@router.patch("/api/schedule/tasks/{task_name}/toggle")
async def toggle_schedule(
    task_name: str,
    req: ScheduleToggleRequest,
    services: WebServices = Depends(get_services),
) -> dict[str, bool]:
    ok = services.task_scheduler.toggle_task(task_name, req.enabled)
    if not ok:
        raise HTTPException(status_code=404, detail="Task not found")
    return {"success": True}


@router.post("/api/schedule/tasks")
async def create_schedule_task(
    req: ScheduledTaskCreate,
    services: WebServices = Depends(get_services),
) -> dict[str, object]:
    task = ScheduledTask(
        name=req.name,
        description=req.description,
        cron=req.cron,
        prompt=req.prompt,
        enabled=req.enabled,
    )
    services.task_scheduler.add_task(task)
    return {"success": True, "task": task.to_dict()}


@router.delete("/api/schedule/tasks/{task_name}")
async def delete_schedule_task(
    task_name: str,
    services: WebServices = Depends(get_services),
) -> dict[str, bool]:
    ok = services.task_scheduler.remove_task(task_name)
    if not ok:
        raise HTTPException(status_code=404, detail="Task not found")
    return {"success": True}


@router.get("/api/uv/envs")
async def list_uv_envs(services: WebServices = Depends(get_services)) -> dict[str, object]:
    return {"envs": services.uv_env_mgr.list_envs()}


@router.get("/api/uv/envs/{name}")
async def get_uv_env(
    name: str,
    services: WebServices = Depends(get_services),
) -> dict[str, object]:
    info = services.uv_env_mgr.get_env(name)
    if not info:
        raise HTTPException(status_code=404, detail="环境不存在")
    return info


@router.post("/api/uv/envs")
async def create_uv_env(
    req: UvEnvCreateRequest,
    services: WebServices = Depends(get_services),
) -> dict[str, object]:
    result = services.uv_env_mgr.create_env(
        name=req.name,
        description=req.description,
        python_version=req.python_version,
        tags=req.tags,
    )
    if not result["success"]:
        raise HTTPException(status_code=400, detail=result["error"])
    return result


@router.delete("/api/uv/envs/{name}")
async def delete_uv_env(
    name: str,
    services: WebServices = Depends(get_services),
) -> dict[str, object]:
    result = services.uv_env_mgr.delete_env(name)
    if not result["success"]:
        raise HTTPException(status_code=404, detail=result["error"])
    return result


@router.patch("/api/uv/envs/{name}")
async def update_uv_env(
    name: str,
    req: UvEnvUpdateRequest,
    services: WebServices = Depends(get_services),
) -> dict[str, object]:
    result = services.uv_env_mgr.update_env_meta(name, description=req.description, tags=req.tags)
    if not result["success"]:
        raise HTTPException(status_code=404, detail=result["error"])
    return result


@router.post("/api/uv/envs/{name}/install")
async def install_uv_packages(
    name: str,
    req: UvPackageRequest,
    services: WebServices = Depends(get_services),
) -> dict[str, object]:
    result = services.uv_env_mgr.install_packages(name, req.packages)
    if not result["success"]:
        raise HTTPException(status_code=400, detail=result["error"])
    return result


@router.post("/api/uv/envs/{name}/uninstall")
async def uninstall_uv_packages(
    name: str,
    req: UvPackageRequest,
    services: WebServices = Depends(get_services),
) -> dict[str, object]:
    result = services.uv_env_mgr.uninstall_packages(name, req.packages)
    if not result["success"]:
        raise HTTPException(status_code=400, detail=result["error"])
    return result


@router.post("/api/uv/envs/{name}/run")
async def run_in_uv_env(
    name: str,
    req: UvRunCodeRequest,
    services: WebServices = Depends(get_services),
) -> dict[str, object]:
    return services.uv_env_mgr.run_script(name, req.code, timeout=req.timeout)


@router.post("/api/uv/envs/{name}/run-file")
async def run_file_in_uv_env(
    name: str,
    req: UvRunFileRequest,
    services: WebServices = Depends(get_services),
) -> dict[str, object]:
    return services.uv_env_mgr.run_file(name, req.filepath, timeout=req.timeout)


@router.get("/api/templates")
async def api_list_templates() -> dict[str, object]:
    return {"templates": list_templates(), "by_category": get_templates_by_category()}


@router.post("/api/upload")
async def upload_file(
    file: UploadFile = File(...),
    services: WebServices = Depends(get_services),
) -> dict[str, object]:
    safe_name = file.filename.replace("/", "_").replace("\\", "_")
    destination = services.paths.uploads_dir / safe_name
    if destination.exists():
        stem, suffix = destination.stem, destination.suffix
        destination = services.paths.uploads_dir / f"{stem}_{uuid.uuid4().hex[:6]}{suffix}"
    contents = await file.read()
    destination.write_bytes(contents)
    return {
        "success": True,
        "filename": destination.name,
        "path": str(destination),
        "size": len(contents),
    }


@router.get("/api/uploads")
async def list_uploads(services: WebServices = Depends(get_services)) -> dict[str, object]:
    files = []
    if services.paths.uploads_dir.exists():
        for entry in services.paths.uploads_dir.iterdir():
            if entry.is_file():
                files.append({"name": entry.name, "size": entry.stat().st_size})
    return {"files": files}
