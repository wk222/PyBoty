"""Agent and tool administration APIs."""

from __future__ import annotations

import logging
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from core.agent_capability_profile import AgentCapabilityProfile, list_capability_presets
from core.agent_middleware_profile import AgentMiddlewareProfile, list_middleware_presets
from core.agent_storage import AgentStorage
from core.agent_tool_inventory import build_agent_tool_inventory
from core.agent_tool_sync import AgentToolSyncError, sync_agent_tool
from core.subagent_governance import build_subagent_governance_snapshot
from core.subagent_sandbox import list_sandbox_adapters
from core.tool_storage import ToolStorage
from web.dependencies import get_services
from web.state import WebServices

logger = logging.getLogger(__name__)
router = APIRouter(tags=["admin"])
SERVICES_DEPENDENCY = Depends(get_services)


def _require_agent(services: WebServices) -> Any:
    """Get system agent, raising 503 if LLM is not configured."""
    try:
        return services.system_agent()
    except Exception as exc:
        api_key_hint = "OPENAI_API_KEY"
        msg = str(exc)
        if "api_key" in msg.lower() or api_key_hint in msg:
            raise HTTPException(
                status_code=503,
                detail=(
                    "LLM not configured. Set the OPENAI_API_KEY environment variable "
                    "or run `pybot-onboard` to configure your API key."
                ),
            ) from exc
        logger.exception("Failed to create system agent")
        raise HTTPException(status_code=500, detail=msg) from exc


class AgentToggleRequest(BaseModel):
    enabled: bool


class AssignToolRequest(BaseModel):
    tool_name: str


class AgentCapabilityProfileRequest(BaseModel):
    capability_profile: dict[str, object]
    middleware_profile: dict[str, object] | None = None


class SyncToolRequest(BaseModel):
    direction: Literal["to_global", "from_global"]
    overwrite: bool = False


@router.get("/api/agents")
async def list_all_agents(services: WebServices = SERVICES_DEPENDENCY) -> dict[str, object]:
    if not services.llm_configured:
        storage = AgentStorage(base_dir=str(services.paths.agents_dir))
        agents_map = storage.list_agents()
        return {"agents": [{"name": name, "description": desc} for name, desc in agents_map.items()]}
    agent = _require_agent(services)
    return {"agents": agent.get_agent_details()}


@router.get("/api/agents/governance/options")
async def get_governance_options() -> dict[str, object]:
    return {
        "capability_presets": list_capability_presets(),
        "middleware_presets": list_middleware_presets(),
        "sandbox_adapters": list_sandbox_adapters(),
        "control_modes": ["inherit", "strict", "balanced", "open"],
    }


@router.get("/api/agents/{agent_name}")
async def get_agent_detail(
    agent_name: str,
    services: WebServices = SERVICES_DEPENDENCY,
) -> dict[str, object]:
    system_agent = _require_agent(services)
    agent_def = system_agent.agent_storage.get_agent(agent_name)
    if not agent_def:
        raise HTTPException(status_code=404, detail="Agent not found")
    local_tool_storage = ToolStorage(str(system_agent.agent_storage.tools_dir_for(agent_name)))
    return {
        **agent_def.to_dict(),
        "tool_inventory": build_agent_tool_inventory(
            agent_def=agent_def,
            global_tool_storage=system_agent.storage,
            local_tool_storage=local_tool_storage,
        ),
        "governance": build_subagent_governance_snapshot(
            base_policy=system_agent.control_policy,
            capability_profile=AgentCapabilityProfile.from_value(agent_def.capability_profile),
            middleware_profile=AgentMiddlewareProfile.from_value(agent_def.middleware_profile),
        ),
    }


@router.patch("/api/agents/{agent_name}/toggle")
async def toggle_agent(
    agent_name: str,
    req: AgentToggleRequest,
    services: WebServices = SERVICES_DEPENDENCY,
) -> dict[str, object]:
    ok = _require_agent(services).agent_storage.toggle_agent(agent_name, req.enabled)
    if not ok:
        raise HTTPException(status_code=404, detail="Agent not found")
    return {"success": True, "agent_name": agent_name, "enabled": req.enabled}


@router.patch("/api/agents/{agent_name}/capabilities")
async def update_agent_capabilities(
    agent_name: str,
    req: AgentCapabilityProfileRequest,
    services: WebServices = SERVICES_DEPENDENCY,
) -> dict[str, object]:
    system_agent = _require_agent(services)
    profile = AgentCapabilityProfile.from_value(req.capability_profile).to_dict()
    updates: dict[str, object] = {"capability_profile": profile}
    middleware_profile = None
    if req.middleware_profile is not None:
        middleware_profile = AgentMiddlewareProfile.from_value(req.middleware_profile).to_dict()
        updates["middleware_profile"] = middleware_profile
    ok = system_agent.agent_storage.update_agent(agent_name, updates)
    if not ok:
        raise HTTPException(status_code=404, detail="Agent not found")
    return {
        "success": True,
        "agent_name": agent_name,
        "capability_profile": profile,
        "middleware_profile": middleware_profile,
        "governance": build_subagent_governance_snapshot(
            base_policy=system_agent.control_policy,
            capability_profile=AgentCapabilityProfile.from_value(profile),
            middleware_profile=AgentMiddlewareProfile.from_value(
                middleware_profile or system_agent.agent_storage.get_agent(agent_name).middleware_profile
            ),
        ),
    }


@router.delete("/api/agents/{agent_name}")
async def delete_agent(
    agent_name: str,
    services: WebServices = SERVICES_DEPENDENCY,
) -> dict[str, object]:
    ok = _require_agent(services).agent_storage.remove_agent(agent_name)
    if not ok:
        raise HTTPException(status_code=404, detail="Agent not found")
    return {"success": True, "deleted": agent_name}


@router.get("/api/agents/{agent_name}/tools")
async def list_agent_tools(
    agent_name: str,
    services: WebServices = SERVICES_DEPENDENCY,
) -> dict[str, object]:
    system_agent = _require_agent(services)
    agent_def = system_agent.agent_storage.get_agent(agent_name)
    if not agent_def:
        raise HTTPException(status_code=404, detail="Agent not found")
    local_tool_storage = ToolStorage(str(system_agent.agent_storage.tools_dir_for(agent_name)))
    tool_inventory = build_agent_tool_inventory(
        agent_def=agent_def,
        global_tool_storage=system_agent.storage,
        local_tool_storage=local_tool_storage,
    )
    return {
        "agent_name": agent_name,
        "enabled": agent_def.enabled,
        "tool_inventory": tool_inventory,
        "tools": tool_inventory["assigned_global_tool_names"],
    }


@router.post("/api/agents/{agent_name}/tools")
async def assign_tool_to_agent(
    agent_name: str,
    req: AssignToolRequest,
    services: WebServices = SERVICES_DEPENDENCY,
) -> dict[str, object]:
    agent = _require_agent(services)
    global_tools = agent.storage.list_tools()
    if req.tool_name not in global_tools:
        raise HTTPException(status_code=404, detail=f"Global tool '{req.tool_name}' not found")
    ok = agent.agent_storage.add_tool_to_agent(agent_name, req.tool_name)
    if not ok:
        raise HTTPException(status_code=404, detail="Agent not found")
    agent_def = agent.agent_storage.get_agent(agent_name)
    if not agent_def:
        raise HTTPException(status_code=404, detail="Agent not found")
    local_tool_storage = ToolStorage(str(agent.agent_storage.tools_dir_for(agent_name)))
    return {
        "success": True,
        "agent_name": agent_name,
        "tool_assigned": req.tool_name,
        "tool_inventory": build_agent_tool_inventory(
            agent_def=agent_def,
            global_tool_storage=agent.storage,
            local_tool_storage=local_tool_storage,
        ),
    }


@router.delete("/api/agents/{agent_name}/tools/{tool_name}")
async def remove_tool_from_agent(
    agent_name: str,
    tool_name: str,
    services: WebServices = SERVICES_DEPENDENCY,
) -> dict[str, object]:
    system_agent = _require_agent(services)
    ok = system_agent.agent_storage.remove_tool_from_agent(agent_name, tool_name)
    if not ok:
        raise HTTPException(status_code=404, detail="Agent or tool not found")
    agent_def = system_agent.agent_storage.get_agent(agent_name)
    if not agent_def:
        raise HTTPException(status_code=404, detail="Agent not found")
    local_tool_storage = ToolStorage(str(system_agent.agent_storage.tools_dir_for(agent_name)))
    return {
        "success": True,
        "agent_name": agent_name,
        "tool_removed": tool_name,
        "tool_inventory": build_agent_tool_inventory(
            agent_def=agent_def,
            global_tool_storage=system_agent.storage,
            local_tool_storage=local_tool_storage,
        ),
    }


@router.post("/api/agents/{agent_name}/tools/{tool_name}/sync")
async def sync_agent_tool_route(
    agent_name: str,
    tool_name: str,
    req: SyncToolRequest,
    services: WebServices = SERVICES_DEPENDENCY,
) -> dict[str, object]:
    system_agent = _require_agent(services)
    try:
        return sync_agent_tool(
            agent_storage=system_agent.agent_storage,
            global_tool_storage=system_agent.storage,
            agent_name=agent_name,
            tool_name=tool_name,
            direction=req.direction,
            overwrite=req.overwrite,
        )
    except AgentToolSyncError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc


@router.get("/api/tools")
async def list_all_tools(services: WebServices = SERVICES_DEPENDENCY) -> dict[str, list[dict]]:
    if not services.llm_configured:
        storage = ToolStorage(base_dir=str(services.paths.global_tools_dir))
        tools = storage.list_tools()
        return {"tools": [{"name": name, "description": desc, "usage_count": 0} for name, desc in tools.items()]}
    agent = _require_agent(services)
    tools = agent.storage.list_tools()
    usage = agent.get_tool_usage_stats()
    return {
        "tools": [
            {"name": name, "description": description, "usage_count": usage.get(name, 0)}
            for name, description in tools.items()
        ]
    }


@router.delete("/api/tools/{tool_name}")
async def delete_tool(
    tool_name: str,
    services: WebServices = SERVICES_DEPENDENCY,
) -> dict[str, object]:
    ok = _require_agent(services).storage.remove_tool(tool_name)
    if not ok:
        raise HTTPException(status_code=404, detail="Tool not found")
    return {"success": True, "deleted": tool_name}


@router.get("/api/agent-control")
async def get_agent_control(services: WebServices = SERVICES_DEPENDENCY) -> dict[str, object]:
    return _require_agent(services).get_control_snapshot()


# --- Phase 8: Debug Panel APIs ---


@router.get("/api/debug/cost")
async def get_cost_summary(services: WebServices = SERVICES_DEPENDENCY) -> dict[str, object]:
    """Get LLM cost and usage summary for the debug panel."""
    try:
        from core.cost_tracker import CostTracker

        persist_path = str(services.paths.workspace_dir / "cost_tracker.json")
        tracker = CostTracker(persist_path=persist_path)
        return {"cost_summary": tracker.get_summary().to_dict()}
    except Exception as exc:
        return {"cost_summary": {}, "error": str(exc)}


@router.get("/api/debug/tasks")
async def get_task_status(services: WebServices = SERVICES_DEPENDENCY) -> dict[str, object]:
    """Get background task queue status."""
    try:
        queue = getattr(services, "_task_queue", None)
        if queue is None:
            return {"tasks": [], "summary": {}}
        return {
            "tasks": [
                {
                    "task_id": t.task_id,
                    "name": t.name,
                    "status": t.status.value,
                    "created_at": t.created_at,
                    "started_at": t.started_at,
                    "completed_at": t.completed_at,
                    "error": t.error,
                }
                for t in queue.list_all()
            ],
            "summary": queue.get_summary(),
        }
    except Exception as exc:
        return {"tasks": [], "summary": {}, "error": str(exc)}


@router.get("/api/debug/mcp")
async def get_mcp_status(services: WebServices = SERVICES_DEPENDENCY) -> dict[str, object]:
    """Get MCP server connection status."""
    try:
        agent = _require_agent(services)
        runtime = getattr(agent, "runtime", None)
        if runtime is None:
            return {"servers": {}}
        mcp_hub = getattr(runtime, "mcp_hub", None)
        if mcp_hub is None:
            return {"servers": {}}
        return {
            "servers": mcp_hub.get_server_status(),
            "tools": [
                {"name": d.name, "description": d.description, "server": d.server_name}
                for d in mcp_hub.get_all_tool_descriptors()
            ],
            "resources": [
                {"uri": r.uri, "name": r.name, "server": r.server_name} for r in mcp_hub.get_all_resource_descriptors()
            ],
        }
    except Exception as exc:
        return {"servers": {}, "error": str(exc)}


@router.get("/api/debug/memory")
async def get_memory_status(services: WebServices = SERVICES_DEPENDENCY) -> dict[str, object]:
    """Get memory system status for debug panel."""
    try:
        from core.semantic_memory import SemanticMemoryManager

        mem = services.memory_mgr
        if isinstance(mem, SemanticMemoryManager):
            return {"memory": mem.get_memory_stats()}
        return {
            "memory": {
                "file_based": True,
                "vector_backed": False,
                "file_lines": len(mem.load().split("\n")),
            }
        }
    except Exception as exc:
        return {"memory": {}, "error": str(exc)}


@router.get("/api/debug/rag")
async def get_rag_status(services: WebServices = SERVICES_DEPENDENCY) -> dict[str, object]:
    """Get RAG knowledge base status."""
    try:
        agent = _require_agent(services)
        runtime = getattr(agent, "runtime", None)
        if runtime is None:
            return {"rag": {"enabled": False}}
        knowledge_tools = getattr(runtime, "knowledge_tools", [])
        return {
            "rag": {
                "enabled": len(knowledge_tools) > 0,
                "tools_count": len(knowledge_tools),
            }
        }
    except Exception as exc:
        return {"rag": {"enabled": False}, "error": str(exc)}


@router.get("/api/debug/providers")
async def get_provider_status() -> dict[str, object]:
    """Get LLM provider availability status."""
    try:
        from core.model_resolver import list_all_providers

        return {"providers": list_all_providers()}
    except Exception as exc:
        return {"providers": {}, "error": str(exc)}


# --- LLM Config endpoints ---


@router.get("/api/config/llm")
async def get_llm_config_api() -> dict[str, object]:
    """Get current LLM configuration."""
    try:
        from core.config import get_llm_config, get_llm_fallback_config, get_observability_config, get_rag_config

        llm = get_llm_config()
        safe_llm = {**llm}
        if safe_llm.get("api_key"):
            key = str(safe_llm["api_key"])
            safe_llm["api_key"] = key[:8] + "..." + key[-4:] if len(key) > 12 else "***"
        return {
            "llm_config": safe_llm,
            "llm_fallback": get_llm_fallback_config(),
            "observability": get_observability_config(),
            "rag_config": get_rag_config(),
        }
    except Exception as exc:
        return {"error": str(exc)}


@router.put("/api/config/llm")
async def update_llm_config_api(request: Request) -> dict[str, object]:
    """Update LLM configuration. Merges with existing config."""
    try:
        from core.config import get_config, save_config

        body = await request.json()
        current = get_config()

        if "llm_config" in body:
            updates = body["llm_config"]
            if updates.get("api_key") and "..." in str(updates["api_key"]):
                updates.pop("api_key")
            current.setdefault("llm_config", {}).update(updates)

        if "llm_fallback" in body:
            current["llm_fallback"] = body["llm_fallback"]

        if "observability" in body:
            current.setdefault("observability", {}).update(body["observability"])

        if "rag_config" in body:
            current.setdefault("rag_config", {}).update(body["rag_config"])

        save_config(current)
        return {"success": True, "message": "Configuration saved. Restart may be needed for some changes."}
    except Exception as exc:
        return {"success": False, "error": str(exc)}


@router.post("/api/config/llm/test")
async def test_llm_connection(request: Request) -> dict[str, object]:
    """Test LLM connection with current or provided config."""
    try:
        body = await request.json()
        provider = body.get("provider", "openai")
        api_key = body.get("api_key", "")
        api_base = body.get("api_base", "")
        model = body.get("model", "gpt-4")

        if not api_key or "..." in api_key:
            from core.config import get_llm_config
            cfg = get_llm_config()
            api_key = api_key if (api_key and "..." not in api_key) else cfg.get("api_key", "")
            if not api_base:
                api_base = cfg.get("api_base", "")

        from core.model_resolver import resolve_chat_model
        llm = resolve_chat_model(
            model_name=model,
            provider=provider,
            api_key=api_key,
            api_base=api_base or None,
            temperature=0.1,
        )
        result = llm.invoke("Reply with exactly: CONNECTION_OK")
        content = result.content if hasattr(result, "content") else str(result)
        return {
            "success": True,
            "response_preview": content[:200],
            "model": model,
            "provider": provider,
        }
    except Exception as exc:
        return {"success": False, "error": str(exc)}


# --- Feedback / Training endpoints ---

_feedback_store = None


def _get_feedback_store():
    global _feedback_store
    if _feedback_store is None:
        from core.training import FeedbackStore

        _feedback_store = FeedbackStore("workspace/feedback_store.json")
    return _feedback_store


@router.post("/api/feedback")
async def submit_feedback(request: Request) -> dict[str, object]:
    """Submit feedback for an agent's output."""
    from core.training import FeedbackRecord

    try:
        body = await request.json()
        record = FeedbackRecord(
            agent_name=body["agent_name"],
            task_summary=body["task_summary"],
            output_summary=body.get("output_summary", ""),
            score=int(body["score"]),
            feedback_text=body.get("feedback_text", ""),
        )
        _get_feedback_store().add(record)
        return {"success": True}
    except Exception as exc:
        return {"success": False, "error": str(exc)}


@router.get("/api/feedback/{agent_name}")
async def get_feedback(agent_name: str) -> dict[str, object]:
    """Get feedback history for an agent."""
    try:
        store = _get_feedback_store()
        records = store.get_for_agent(agent_name, limit=20)
        return {
            "agent_name": agent_name,
            "count": len(records),
            "records": [
                {
                    "task_summary": r.task_summary,
                    "output_summary": r.output_summary,
                    "score": r.score,
                    "feedback_text": r.feedback_text,
                    "timestamp": r.timestamp,
                }
                for r in records
            ],
        }
    except Exception as exc:
        return {"agent_name": agent_name, "records": [], "error": str(exc)}
