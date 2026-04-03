"""Session spine APIs."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from web.dependencies import get_services
from web.state import WebServices

router = APIRouter(tags=["sessions"])
SERVICES_DEPENDENCY = Depends(get_services)


class SessionSummaryRequest(BaseModel):
    summary: str
    layer: str = "session"


class SessionNoteRequest(BaseModel):
    note: str
    layer: str = "session"
    memory_type: str = "session_note"
    durable: bool = False
    occurred_on: str = ""
    verified: bool = False


class SessionCompactRequest(BaseModel):
    reason: str = "manual"


class SessionArtifactInvalidateRequest(BaseModel):
    reason: str = "manual"
    scopes: list[str] = []


class SessionPromptInjectionRequest(BaseModel):
    prompt_injection: str = ""


class SessionModeSwitchRequest(BaseModel):
    mode: str


@router.get("/api/sessions")
async def list_sessions(
    services: WebServices = SERVICES_DEPENDENCY,
) -> dict[str, list[dict[str, Any]]]:
    services.sync_session_spine()
    return {"sessions": services.session_runtime.list_sessions()}


@router.get("/api/sessions/{session_key}")
async def get_session(
    session_key: str,
    services: WebServices = SERVICES_DEPENDENCY,
) -> dict[str, Any]:
    services.sync_session_spine()
    session = services.session_runtime.get_session(session_key)
    if session is None:
        raise HTTPException(status_code=404, detail="会话不存在")
    return {"session": session}


@router.get("/api/sessions/{session_key}/overview")
async def get_session_overview(
    session_key: str,
    services: WebServices = SERVICES_DEPENDENCY,
) -> dict[str, Any]:
    services.sync_session_spine()
    overview = services.session_runtime.get_overview(session_key)
    if overview is None:
        raise HTTPException(status_code=404, detail="会话不存在")
    return {"overview": overview}


@router.get("/api/sessions/{session_key}/timeline")
async def get_session_timeline(
    session_key: str,
    kind: str = Query(default=""),
    limit: int = Query(default=100, ge=1, le=500),
    services: WebServices = SERVICES_DEPENDENCY,
) -> dict[str, Any]:
    services.sync_session_spine()
    session = services.session_runtime.get_session(session_key)
    if session is None:
        raise HTTPException(status_code=404, detail="会话不存在")
    return {
        "session_key": session_key,
        "timeline": services.session_runtime.get_timeline(session_key, kind=kind, limit=limit),
    }


@router.get("/api/sessions/{session_key}/events")
async def get_session_events(
    session_key: str,
    op: str = Query(default=""),
    limit: int = Query(default=200, ge=1, le=500),
    services: WebServices = SERVICES_DEPENDENCY,
) -> dict[str, Any]:
    services.sync_session_spine()
    session = services.session_runtime.get_session(session_key)
    if session is None:
        raise HTTPException(status_code=404, detail="会话不存在")
    return {
        "session_key": session_key,
        "events": services.session_runtime.get_event_log(session_key, op=op, limit=limit),
    }


@router.get("/api/sessions/{session_key}/file-views")
async def get_session_file_views(
    session_key: str,
    limit: int = Query(default=100, ge=1, le=500),
    services: WebServices = SERVICES_DEPENDENCY,
) -> dict[str, Any]:
    services.sync_session_spine()
    session = services.session_runtime.get_session(session_key)
    if session is None:
        raise HTTPException(status_code=404, detail="会话不存在")
    return {
        "session_key": session_key,
        "file_views": services.session_runtime.get_file_views(session_key, limit=limit),
    }


@router.get("/api/sessions/{session_key}/kernel")
async def get_session_kernel(
    session_key: str,
    services: WebServices = SERVICES_DEPENDENCY,
) -> dict[str, Any]:
    services.sync_session_spine()
    kernel = services.session_runtime.get_kernel_snapshot(session_key)
    if kernel is None:
        raise HTTPException(status_code=404, detail="会话不存在")
    return {"session_key": session_key, "kernel": kernel}


@router.get("/api/sessions/{session_key}/sidechains")
async def get_session_sidechains(
    session_key: str,
    services: WebServices = SERVICES_DEPENDENCY,
) -> dict[str, Any]:
    services.sync_session_spine()
    session = services.session_runtime.get_session(session_key)
    if session is None:
        raise HTTPException(status_code=404, detail="会话不存在")
    return {
        "session_key": session_key,
        "sidechains": services.session_runtime.get_sidechains(session_key),
    }


@router.get("/api/sessions/{session_key}/artifacts")
async def get_session_artifacts(
    session_key: str,
    services: WebServices = SERVICES_DEPENDENCY,
) -> dict[str, Any]:
    services.sync_session_spine()
    artifacts = services.session_runtime.get_compiled_artifacts(session_key)
    if artifacts is None:
        raise HTTPException(status_code=404, detail="会话不存在")
    return {"session_key": session_key, "artifacts": artifacts}


@router.post("/api/sessions/{session_key}/summary")
async def update_session_summary(
    session_key: str,
    request: SessionSummaryRequest,
    services: WebServices = SERVICES_DEPENDENCY,
) -> dict[str, Any]:
    try:
        session = services.session_runtime.update_summary(
            session_key,
            summary=request.summary,
            layer=request.layer,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="会话不存在") from exc
    return {"session": session}


@router.post("/api/sessions/{session_key}/notes")
async def add_session_note(
    session_key: str,
    request: SessionNoteRequest,
    services: WebServices = SERVICES_DEPENDENCY,
) -> dict[str, Any]:
    try:
        session = services.session_runtime.remember(
            session_key,
            note=request.note,
            layer=request.layer,
            memory_type=request.memory_type,
            durable=request.durable,
            occurred_on=request.occurred_on,
            verified=request.verified,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="会话不存在") from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"session": session}


@router.post("/api/sessions/{session_key}/compact")
async def compact_session_context(
    session_key: str,
    request: SessionCompactRequest,
    services: WebServices = SERVICES_DEPENDENCY,
) -> dict[str, Any]:
    try:
        session = services.session_runtime.compact_session(session_key, reason=request.reason)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="会话不存在") from exc
    return {"session": session}


@router.post("/api/sessions/{session_key}/artifacts/invalidate")
async def invalidate_session_artifacts(
    session_key: str,
    request: SessionArtifactInvalidateRequest,
    services: WebServices = SERVICES_DEPENDENCY,
) -> dict[str, Any]:
    try:
        result = services.session_runtime.invalidate_artifacts(
            session_key,
            reason=request.reason,
            scopes=request.scopes,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="会话不存在") from exc
    return result


@router.post("/api/sessions/{session_key}/prompt-injection")
async def set_session_prompt_injection(
    session_key: str,
    request: SessionPromptInjectionRequest,
    services: WebServices = SERVICES_DEPENDENCY,
) -> dict[str, Any]:
    try:
        session = services.session_runtime.set_prompt_injection(
            session_key,
            prompt_injection=request.prompt_injection,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="会话不存在") from exc
    return {"session": session}


@router.post("/api/sessions/checkpoint/rebuild")
async def rebuild_session_checkpoint(
    services: WebServices = SERVICES_DEPENDENCY,
) -> dict[str, Any]:
    services.sync_session_spine()
    return {"checkpoint": services.session_runtime.rebuild_checkpoint()}


@router.post("/api/sessions/{session_key}/mode")
async def switch_session_mode(
    session_key: str,
    request: SessionModeSwitchRequest,
    services: WebServices = SERVICES_DEPENDENCY,
) -> dict[str, Any]:
    try:
        result = services.session_runtime.switch_mode(session_key, new_mode=request.mode)
    except KeyError:
        raise HTTPException(status_code=404, detail="会话不存在")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"session_key": session_key, **result}


@router.get("/api/sessions/{session_key}/budget")
async def get_session_budget(
    session_key: str,
    model_name: str = Query(default=""),
    services: WebServices = SERVICES_DEPENDENCY,
) -> dict[str, Any]:
    from core.systems.runtime.context_budget import ContextBudgetManager

    session = services.session_runtime.get_session(session_key)
    if session is None:
        raise HTTPException(status_code=404, detail="会话不存在")

    budget_mgr = ContextBudgetManager(model_name=model_name)
    assessment = budget_mgr.assess(session)
    return {"session_key": session_key, "budget": assessment.to_dict()}


@router.get("/api/sessions/{session_key}/status")
async def get_session_status(
    session_key: str,
    model_name: str = Query(default=""),
    services: WebServices = SERVICES_DEPENDENCY,
) -> dict[str, Any]:
    from core.systems.runtime.context_budget import ContextBudgetManager

    services.sync_session_spine()
    session = services.session_runtime.get_session(session_key)
    if session is None:
        raise HTTPException(status_code=404, detail="会话不存在")

    budget_mgr = ContextBudgetManager(model_name=model_name)
    assessment = budget_mgr.assess(session)

    kernel_sidechains: list[str] = []
    try:
        raw = services.session_runtime.get_sidechains(session_key)
        kernel_sidechains = [f"{sc.get('purpose', '')}:{sc.get('status', '')}" for sc in raw]
    except Exception:
        pass

    return {
        "session_key": session_key,
        "thread_id": session.get("thread_id", ""),
        "mode": session.get("primary_mode", "assistant"),
        "mode_history": list(session.get("mode_history", [])),
        "status": session.get("status", "active"),
        "message_count": session.get("message_count", 0),
        "last_message_at": session.get("last_message_at"),
        "timeline_events": len(session.get("timeline", [])),
        "sidechains": kernel_sidechains,
        "budget": assessment.to_dict(),
    }
