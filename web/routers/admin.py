"""Agent and tool administration APIs."""

from __future__ import annotations

import logging
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from core.assets.agents import (
    AgentCapabilityProfile,
    AgentMiddlewareProfile,
    AgentStorage,
    AgentToolSyncError,
    build_agent_tool_inventory,
    build_subagent_governance_snapshot,
    list_capability_presets,
    list_middleware_presets,
    list_sandbox_adapters,
    sync_agent_tool,
)
from core.assets.tools import ToolStorage, execute_tool_script
from core.modes.builtin_packs import ensure_builtin_packs
from core.modes.pack import get_global_registry
from core.system_model import build_system_model
from core.systems.governance import AgentControlPolicy
from core.systems.integration import discover_plugins, get_plugin_registry, reset_plugin_registry
from core.systems.memory import SemanticMemoryManager
from core.systems.runtime import (
    get_config,
    get_gateway_config,
    get_llm_config,
    get_llm_fallback_config,
    get_observability_config,
    get_rag_config,
    save_config,
)
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


class AppMatrixSyncRequest(BaseModel):
    clear_missing: bool = False


class AppMatrixNodeMetadataRequest(BaseModel):
    shared_datastores: list[str] | None = None
    shared_schemas: list[dict[str, object]] | None = None
    data_contracts: list[dict[str, object]] | None = None


class AppMatrixServiceDiscoveryRequest(BaseModel):
    query: str = ""
    provides: str = ""


class AppMatrixServiceGrantRequest(BaseModel):
    caller_app: str
    target_app: str = ""
    capability_name: str = ""
    provides: str = ""
    requested_quota: int = 1
    ttl_seconds: int = 3600
    metadata: dict[str, object] | None = None


class AppMatrixServiceInvokeRequest(BaseModel):
    caller_app: str
    grant_token: str
    action: str
    payload: dict[str, object] = {}


class PromoteCapabilityGapRequest(BaseModel):
    auto_start: bool = True


class DraftCapabilityGapRequest(BaseModel):
    target_name: str = ""
    overwrite: bool = False


class PublishCapabilityGapRequest(BaseModel):
    publish_to_hub: bool = False
    hub_url: str = ""
    hub_token: str = ""
    version: str = "0.1.0"
    changelog: str = ""


class CloseCapabilityGapRequest(BaseModel):
    target_name: str = ""
    overwrite: bool = False
    publish_to_hub: bool = False
    hub_url: str = ""
    hub_token: str = ""
    version: str = "0.1.0"
    changelog: str = ""


class StartCapabilityGapRolloutRequest(BaseModel):
    strategy: str = "shadow"
    target: str = ""
    note: str = ""


class EvaluateCapabilityGapRolloutRequest(BaseModel):
    outcome: str
    note: str = ""
    rollout_id: str = ""
    telemetry_sample: dict[str, object] | None = None
    close_on_healthy: bool = True


class PluginDiscoverRequest(BaseModel):
    directories: list[str] | None = None
    reset: bool = False
    autoload_enabled: bool = True


def _default_plugin_directories(services: WebServices) -> list[str]:
    return [
        str(services.paths.root_dir / "plugins"),
        str(services.paths.runtime_root_dir / "plugins"),
        str(services.paths.workspace_dir / "plugins"),
    ]


def _governance_policy_payload(services: WebServices) -> dict[str, object]:
    policy = AgentControlPolicy.from_config(services.control_config)
    return {
        "policy": policy.to_dict(),
        "presets": {
            "open": AgentControlPolicy.from_config({"mode": "open"}).to_dict(),
            "balanced": AgentControlPolicy.from_config({"mode": "balanced"}).to_dict(),
            "strict": AgentControlPolicy.from_config({"mode": "strict"}).to_dict(),
        },
    }


def _governance_options_payload() -> dict[str, object]:
    return {
        "capability_presets": list_capability_presets(),
        "middleware_presets": list_middleware_presets(),
        "sandbox_adapters": list_sandbox_adapters(),
        "control_modes": ["inherit", "strict", "balanced", "open"],
    }


def _gateway_supported_channels(services: WebServices) -> list[str]:
    if not services.llm_configured:
        return []
    try:
        channel_manager = services.system_agent().channel_manager
    except Exception:
        return []
    if channel_manager is None or not hasattr(channel_manager, "list_channels"):
        return []
    channels = channel_manager.list_channels()
    if isinstance(channels, dict):
        return sorted(channels.keys())
    return sorted(str(item) for item in channels)


def _gateway_routes_payload(services: WebServices) -> list[dict[str, Any]]:
    if not services.llm_configured:
        return []
    try:
        channel_manager = services.system_agent().channel_manager
    except Exception:
        return []
    if channel_manager is None or not hasattr(channel_manager, "list_routes"):
        return []
    return list(channel_manager.list_routes())


def _gateway_control_plane_payload(services: WebServices) -> dict[str, object]:
    gateway_config = get_gateway_config()
    auth_mode = str(gateway_config.get("auth", {}).get("mode", "none"))
    ws_enabled = bool(gateway_config.get("ws", {}).get("enabled", True))
    pending_pairings = services.gateway_runtime.pairings.list_pending()
    approved_pairings = services.gateway_runtime.pairings.list_approved()
    routes = _gateway_routes_payload(services)
    return {
        "status": {
            "status": "ok",
            "ws_enabled": ws_enabled,
            "auth_mode": auth_mode,
            "supported_channels": _gateway_supported_channels(services),
            "presence_count": len(services.gateway_runtime.presence.list()),
            "pending_pairings": len(pending_pairings),
            "approved_pairings": len(approved_pairings),
            "session_count": len(services.gateway_runtime.sessions.list()),
            "route_count": len(routes),
        },
        "pairings": {
            "pending": pending_pairings,
            "approved": approved_pairings,
        },
        "routes": routes,
    }


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
    return _governance_options_payload()


@router.get("/api/system/model")
async def get_system_model() -> dict[str, object]:
    return build_system_model()


@router.get("/api/system/modes")
async def get_system_modes(services: WebServices = SERVICES_DEPENDENCY) -> dict[str, object]:
    agent = _require_agent(services)
    ensure_builtin_packs()
    registry = get_global_registry()
    return {
        "modes": [
            {
                "name": pack.name,
                "profile": pack.profile.to_dict(),
                "api_methods": sorted(pack.get_api_methods().keys()),
                "prompt_section_preview": pack.get_prompt_section(None)[:200],
            }
            for pack in registry.list_all()
        ],
        "current": agent.get_mode_profile(),
    }


@router.get("/api/plugins")
async def list_plugins(services: WebServices = SERVICES_DEPENDENCY) -> dict[str, object]:
    registry = get_plugin_registry()
    return {
        "plugins": registry.to_dict(),
        "default_directories": _default_plugin_directories(services),
    }


@router.get("/api/plugins/{plugin_id}")
async def get_plugin_detail(
    plugin_id: str,
    services: WebServices = SERVICES_DEPENDENCY,
) -> dict[str, object]:
    registry = get_plugin_registry()
    try:
        plugin = registry.describe_plugin(plugin_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Plugin not found") from exc
    return {
        "plugin": plugin,
        "default_directories": _default_plugin_directories(services),
    }


@router.post("/api/plugins/discover")
async def discover_plugin_manifests(
    req: PluginDiscoverRequest,
    services: WebServices = SERVICES_DEPENDENCY,
) -> dict[str, object]:
    registry = reset_plugin_registry() if req.reset else get_plugin_registry()
    directories = req.directories or _default_plugin_directories(services)
    discovered = discover_plugins(directories, registry=registry)
    load_errors: list[dict[str, str]] = []
    if req.autoload_enabled:
        for manifest in discovered:
            if not manifest.enabled:
                continue
            try:
                registry.load_plugin(manifest.id)
            except Exception as exc:
                load_errors.append({"plugin_id": manifest.id, "error": str(exc)})
    return {
        "directories": directories,
        "discovered": [manifest.to_dict() for manifest in discovered],
        "plugins": registry.to_dict(),
        "load_errors": load_errors,
    }


@router.post("/api/plugins/{plugin_id}/enable")
async def enable_plugin(plugin_id: str) -> dict[str, object]:
    registry = get_plugin_registry()
    try:
        runtime = registry.enable_plugin(plugin_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Plugin not found") from exc
    return {"success": True, "plugin": registry.describe_plugin(plugin_id), "runtime": runtime.to_dict()}


@router.post("/api/plugins/{plugin_id}/disable")
async def disable_plugin(plugin_id: str) -> dict[str, object]:
    registry = get_plugin_registry()
    try:
        runtime = registry.disable_plugin(plugin_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Plugin not found") from exc
    return {"success": True, "plugin": registry.describe_plugin(plugin_id), "runtime": runtime.to_dict()}


@router.post("/api/plugins/{plugin_id}/unload")
async def unload_plugin(plugin_id: str) -> dict[str, object]:
    registry = get_plugin_registry()
    plugin = registry.get(plugin_id)
    if plugin is None:
        raise HTTPException(status_code=404, detail="Plugin not found")
    unloaded = registry.unload_plugin(plugin_id)
    return {"success": unloaded, "plugin": registry.describe_plugin(plugin_id)}


@router.get("/api/app-matrix/overview")
async def get_app_matrix_overview(services: WebServices = SERVICES_DEPENDENCY) -> dict[str, object]:
    runtime = services.app_matrix_runtime()
    runtime.sync_apps()
    return runtime.get_overview()


@router.post("/api/app-matrix/sync")
async def sync_app_matrix_topology(
    req: AppMatrixSyncRequest,
    services: WebServices = SERVICES_DEPENDENCY,
) -> dict[str, object]:
    return services.app_matrix_runtime().sync_apps(clear_missing=req.clear_missing)


@router.get("/api/app-matrix/nodes/{app_name}")
async def get_app_matrix_node_summary(
    app_name: str,
    services: WebServices = SERVICES_DEPENDENCY,
) -> dict[str, object]:
    summary = services.app_matrix_runtime().get_app_summary(app_name)
    if summary is None:
        raise HTTPException(status_code=404, detail="APP Brain node not found")
    return summary


@router.patch("/api/app-matrix/nodes/{app_name}/metadata")
async def update_app_matrix_node_metadata(
    app_name: str,
    req: AppMatrixNodeMetadataRequest,
    services: WebServices = SERVICES_DEPENDENCY,
) -> dict[str, object]:
    runtime = services.app_matrix_runtime()
    try:
        return runtime.update_app_contract_metadata(
            app_name,
            shared_datastores=req.shared_datastores,
            shared_schemas=req.shared_schemas,
            data_contracts=req.data_contracts,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/api/app-matrix/services/discover")
async def discover_app_matrix_services(
    req: AppMatrixServiceDiscoveryRequest,
    services: WebServices = SERVICES_DEPENDENCY,
) -> dict[str, object]:
    return services.app_matrix_runtime().discover_services(query=req.query, provides=req.provides)


@router.post("/api/app-matrix/services/grants")
async def request_app_matrix_service_grant(
    req: AppMatrixServiceGrantRequest,
    services: WebServices = SERVICES_DEPENDENCY,
) -> dict[str, object]:
    result = services.app_matrix_runtime().request_service_grant(
        caller_app=req.caller_app,
        target_app=req.target_app,
        capability_name=req.capability_name,
        provides=req.provides,
        requested_quota=req.requested_quota,
        ttl_seconds=req.ttl_seconds,
        metadata=dict(req.metadata or {}),
    )
    if not result.get("success"):
        raise HTTPException(status_code=400, detail=result.get("error", "Grant request failed"))
    return result


@router.get("/api/app-matrix/services/grants")
async def list_app_matrix_service_grants(
    caller_app: str = "",
    provider_app: str = "",
    services: WebServices = SERVICES_DEPENDENCY,
) -> dict[str, object]:
    grants = services.app_matrix_runtime().list_service_grants(
        caller_app=caller_app,
        provider_app=provider_app,
    )
    return {
        "grants": grants,
        "count": len(grants),
    }


@router.post("/api/app-matrix/services/invoke")
async def invoke_app_matrix_service(
    req: AppMatrixServiceInvokeRequest,
    services: WebServices = SERVICES_DEPENDENCY,
) -> dict[str, object]:
    result = services.app_matrix_runtime().invoke_service(
        caller_app=req.caller_app,
        grant_token=req.grant_token,
        action=req.action,
        payload=dict(req.payload),
    )
    if not result.get("success"):
        raise HTTPException(status_code=400, detail=result.get("error", "Service invocation failed"))
    return result


@router.get("/api/admin/capability-gaps")
async def list_admin_capability_gaps(
    status: str = "",
    services: WebServices = SERVICES_DEPENDENCY,
) -> dict[str, object]:
    agent = services.agents.get_or_create_mode("admin", "__admin_control__")
    candidates = agent.list_capability_gap_candidates(status=status)
    return {"candidates": candidates, "count": len(candidates)}


@router.get("/api/admin/capability-gaps/{candidate_id}")
async def get_admin_capability_gap(
    candidate_id: str,
    services: WebServices = SERVICES_DEPENDENCY,
) -> dict[str, object]:
    agent = services.agents.get_or_create_mode("admin", "__admin_control__")
    candidate = agent.get_capability_gap_candidate(candidate_id)
    if candidate is None:
        raise HTTPException(status_code=404, detail="Capability gap candidate not found")
    return candidate


@router.post("/api/admin/capability-gaps/{candidate_id}/draft")
async def draft_admin_capability_gap(
    candidate_id: str,
    req: DraftCapabilityGapRequest,
    services: WebServices = SERVICES_DEPENDENCY,
) -> dict[str, object]:
    agent = services.agents.get_or_create_mode("admin", "__admin_control__")
    result = agent.draft_capability_gap_candidate(
        candidate_id,
        target_name=req.target_name,
        overwrite=req.overwrite,
    )
    if not result.get("success"):
        raise HTTPException(status_code=400, detail=result.get("error", "Capability gap draft failed"))
    return result


@router.post("/api/admin/capability-gaps/{candidate_id}/validate")
async def validate_admin_capability_gap(
    candidate_id: str,
    services: WebServices = SERVICES_DEPENDENCY,
) -> dict[str, object]:
    agent = services.agents.get_or_create_mode("admin", "__admin_control__")
    result = agent.validate_capability_gap_candidate(candidate_id)
    if not result.get("success"):
        raise HTTPException(status_code=400, detail=result.get("error", "Capability gap validation failed"))
    return result


@router.post("/api/admin/capability-gaps/{candidate_id}/publish")
async def publish_admin_capability_gap(
    candidate_id: str,
    req: PublishCapabilityGapRequest,
    services: WebServices = SERVICES_DEPENDENCY,
) -> dict[str, object]:
    agent = services.agents.get_or_create_mode("admin", "__admin_control__")
    result = agent.publish_capability_gap_candidate(
        candidate_id,
        publish_to_hub=req.publish_to_hub,
        hub_url=req.hub_url,
        hub_token=req.hub_token,
        version=req.version,
        changelog=req.changelog,
    )
    if not result.get("success"):
        raise HTTPException(status_code=400, detail=result.get("error", "Capability gap publish failed"))
    return result


@router.post("/api/admin/capability-gaps/{candidate_id}/close-loop")
async def close_loop_admin_capability_gap(
    candidate_id: str,
    req: CloseCapabilityGapRequest,
    services: WebServices = SERVICES_DEPENDENCY,
) -> dict[str, object]:
    agent = services.agents.get_or_create_mode("admin", "__admin_control__")
    result = agent.close_capability_gap_candidate(
        candidate_id,
        target_name=req.target_name,
        overwrite=req.overwrite,
        publish_to_hub=req.publish_to_hub,
        hub_url=req.hub_url,
        hub_token=req.hub_token,
        version=req.version,
        changelog=req.changelog,
    )
    if not result.get("success"):
        raise HTTPException(status_code=400, detail=result.get("error", "Capability gap close-loop failed"))
    return result


@router.post("/api/admin/capability-gaps/{candidate_id}/rollout")
async def start_admin_capability_gap_rollout(
    candidate_id: str,
    req: StartCapabilityGapRolloutRequest,
    services: WebServices = SERVICES_DEPENDENCY,
) -> dict[str, object]:
    agent = services.agents.get_or_create_mode("admin", "__admin_control__")
    result = agent.start_capability_gap_rollout(
        candidate_id,
        strategy=req.strategy,
        target=req.target,
        note=req.note,
    )
    if not result.get("success"):
        raise HTTPException(status_code=400, detail=result.get("error", "Capability gap rollout failed"))
    return result


@router.post("/api/admin/capability-gaps/{candidate_id}/rollout/evaluate")
async def evaluate_admin_capability_gap_rollout(
    candidate_id: str,
    req: EvaluateCapabilityGapRolloutRequest,
    services: WebServices = SERVICES_DEPENDENCY,
) -> dict[str, object]:
    agent = services.agents.get_or_create_mode("admin", "__admin_control__")
    result = agent.evaluate_capability_gap_rollout(
        candidate_id,
        outcome=req.outcome,
        note=req.note,
        rollout_id=req.rollout_id,
        telemetry_sample=dict(req.telemetry_sample or {}),
        close_on_healthy=req.close_on_healthy,
    )
    if not result.get("success"):
        raise HTTPException(status_code=400, detail=result.get("error", "Capability gap rollout evaluation failed"))
    return result


@router.post("/api/admin/capability-gaps/{candidate_id}/promote")
async def promote_admin_capability_gap(
    candidate_id: str,
    req: PromoteCapabilityGapRequest,
    services: WebServices = SERVICES_DEPENDENCY,
) -> dict[str, object]:
    agent = services.agents.get_or_create_mode("admin", "__admin_control__")
    result = agent.promote_capability_gap_candidate(candidate_id, auto_start=req.auto_start)
    if not result.get("success"):
        raise HTTPException(status_code=404, detail=result.get("error", "Capability gap candidate not found"))
    return result


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


@router.post("/api/tools/{tool_name}/run")
async def run_tool(
    tool_name: str,
    request: Request,
    services: WebServices = SERVICES_DEPENDENCY,
) -> Any:
    storage = ToolStorage(base_dir=str(services.paths.global_tools_dir))
    tool_def = storage.get_tool(tool_name)
    if tool_def is None and services.llm_configured:
        tool_def = _require_agent(services).storage.get_tool(tool_name)
    if tool_def is None:
        raise HTTPException(status_code=404, detail=f"Tool '{tool_name}' not found")

    try:
        body = await request.json()
    except Exception:
        body = {}

    code = tool_def.get("code", "")
    dependencies = list(tool_def.get("dependencies", []))
    result = execute_tool_script(
        tool_name=tool_name,
        code=code,
        dependencies=dependencies,
        kwargs=body,
        project_paths=services.paths,
    )
    if result.get("success"):
        return result.get("result", result)
    raise HTTPException(status_code=500, detail=result.get("error", "Tool execution failed"))


@router.get("/api/agent-control")
async def get_agent_control(services: WebServices = SERVICES_DEPENDENCY) -> dict[str, object]:
    return _require_agent(services).get_control_snapshot()


@router.get("/api/governance/policy")
async def get_governance_policy(services: WebServices = SERVICES_DEPENDENCY) -> dict[str, object]:
    """Read the current agent control policy for visual configuration."""
    return _governance_policy_payload(services)


@router.get("/api/governance/center")
async def get_governance_center(services: WebServices = SERVICES_DEPENDENCY) -> dict[str, object]:
    """Return a unified governance control-plane snapshot for the web console."""
    snapshot = services.approval_queue.get_snapshot()
    return {
        "approvals": {
            "pending": services.approval_queue.list_pending(),
            "recent": services.approval_queue.list_history(limit=25),
            "counts": {
                "pending": snapshot["pending"],
                "approved": snapshot["approved"],
                "rejected": snapshot["rejected"],
            },
        },
        "policy": _governance_policy_payload(services),
        "options": _governance_options_payload(),
        "gateway": _gateway_control_plane_payload(services),
    }


class GovernancePolicyUpdateRequest(BaseModel):
    policy: dict[str, object]


@router.put("/api/governance/policy")
async def update_governance_policy(
    req: GovernancePolicyUpdateRequest,
    services: WebServices = SERVICES_DEPENDENCY,
) -> dict[str, object]:
    """Update the agent control policy and persist to config.json."""
    new_policy_raw = dict(req.policy)

    validated = AgentControlPolicy.from_config(new_policy_raw)

    config = get_config()
    config.setdefault("agent_control", {}).update(new_policy_raw)
    save_config(config)

    services.control_config.update(new_policy_raw)

    return {
        "success": True,
        "policy": validated.to_dict(),
        "message": "策略已保存。新的会话将使用更新后的策略。",
    }


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
        queue = services.task_queue
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
                    "metadata": t.metadata,
                }
                for t in queue.list_all()
            ],
            "summary": queue.get_summary(),
        }
    except Exception as exc:
        return {"tasks": [], "summary": {}, "error": str(exc)}


@router.post("/api/debug/tasks/{task_id}/cancel")
async def cancel_task(task_id: str, services: WebServices = SERVICES_DEPENDENCY) -> dict[str, object]:
    """Cancel a pending or running background task."""
    cancelled = services.task_queue.cancel(task_id)
    if not cancelled:
        raise HTTPException(status_code=404, detail="Task not found or already finished")
    return {"success": True, "task_id": task_id}


@router.get("/api/debug/tasks/{task_id}")
async def get_task_detail(task_id: str, services: WebServices = SERVICES_DEPENDENCY) -> dict[str, object]:
    """Get detailed status for a single background task."""
    info = services.task_queue.get_status(task_id)
    if info is None:
        raise HTTPException(status_code=404, detail="Task not found")
    return {
        "task_id": info.task_id,
        "name": info.name,
        "status": info.status.value,
        "created_at": info.created_at,
        "started_at": info.started_at,
        "completed_at": info.completed_at,
        "error": info.error,
        "metadata": info.metadata,
    }


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
        from core.systems.runtime.model_resolver import list_all_providers

        return {"providers": list_all_providers()}
    except Exception as exc:
        return {"providers": {}, "error": str(exc)}


# --- LLM Config endpoints ---


@router.get("/api/config/llm")
async def get_llm_config_api() -> dict[str, object]:
    """Get current LLM configuration."""
    try:
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
            cfg = get_llm_config()
            api_key = api_key if (api_key and "..." not in api_key) else cfg.get("api_key", "")
            if not api_base:
                api_base = cfg.get("api_base", "")

        from core.systems.runtime.model_resolver import resolve_model

        resolved = resolve_model(
            {"model": model, "provider": provider},
            api_key=api_key,
            base_url=api_base or None,
            temperature=0.1,
        )
        result = resolved.model.invoke("Reply with exactly: CONNECTION_OK")
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


# ── Scheduled Tasks Management ────────────────────────────────────


class ScheduledTaskRequest(BaseModel):
    name: str
    description: str = ""
    cron: str = "*/30 * * * *"
    prompt: str = ""
    enabled: bool = False
    run_once_at: float | None = None


class ScheduledTaskToggle(BaseModel):
    enabled: bool


@router.get("/api/schedules")
async def api_list_schedules(
    services: WebServices = SERVICES_DEPENDENCY,
) -> dict[str, object]:
    """List all scheduled tasks with status info."""
    try:
        scheduler = services.task_scheduler
        tasks = []
        for task in scheduler.tasks.values():
            d = task.to_dict()
            d["next_should_run"] = scheduler._should_run(task) if task.enabled else False
            tasks.append(d)
        return {
            "tasks": tasks,
            "running": scheduler._running,
            "history": scheduler._execution_history[-20:],
        }
    except Exception as exc:
        return {"tasks": [], "error": str(exc)}


@router.post("/api/schedules")
async def api_create_schedule(
    req: ScheduledTaskRequest,
    services: WebServices = SERVICES_DEPENDENCY,
) -> dict[str, object]:
    """Create a new scheduled task."""
    from core.assets.workflows.scheduling import ScheduledTask

    scheduler = services.task_scheduler
    if req.name in scheduler.tasks:
        raise HTTPException(status_code=409, detail=f"Task '{req.name}' already exists")
    task = ScheduledTask(
        name=req.name,
        description=req.description,
        cron=req.cron,
        prompt=req.prompt,
        enabled=req.enabled,
        run_once_at=req.run_once_at,
    )
    scheduler.tasks[req.name] = task
    scheduler._save_run_state()
    return {"success": True, "task": task.to_dict()}


@router.put("/api/schedules/{task_name}")
async def api_update_schedule(
    task_name: str,
    req: ScheduledTaskRequest,
    services: WebServices = SERVICES_DEPENDENCY,
) -> dict[str, object]:
    """Update an existing scheduled task."""
    scheduler = services.task_scheduler
    task = scheduler.tasks.get(task_name)
    if task is None:
        raise HTTPException(status_code=404, detail=f"Task '{task_name}' not found")
    task.description = req.description
    task.cron = req.cron
    task.prompt = req.prompt
    task.enabled = req.enabled
    task.run_once_at = req.run_once_at
    scheduler._save_run_state()
    return {"success": True, "task": task.to_dict()}


@router.patch("/api/schedules/{task_name}/toggle")
async def api_toggle_schedule(
    task_name: str,
    req: ScheduledTaskToggle,
    services: WebServices = SERVICES_DEPENDENCY,
) -> dict[str, object]:
    """Enable or disable a scheduled task."""
    scheduler = services.task_scheduler
    task = scheduler.tasks.get(task_name)
    if task is None:
        raise HTTPException(status_code=404, detail=f"Task '{task_name}' not found")
    task.enabled = req.enabled
    scheduler._save_run_state()
    return {"success": True, "name": task_name, "enabled": req.enabled}


@router.delete("/api/schedules/{task_name}")
async def api_delete_schedule(
    task_name: str,
    services: WebServices = SERVICES_DEPENDENCY,
) -> dict[str, object]:
    """Delete a scheduled task."""
    scheduler = services.task_scheduler
    if task_name not in scheduler.tasks:
        raise HTTPException(status_code=404, detail=f"Task '{task_name}' not found")
    del scheduler.tasks[task_name]
    scheduler._save_run_state()
    return {"success": True, "deleted": task_name}


@router.post("/api/schedules/{task_name}/run")
async def api_run_schedule_now(
    task_name: str,
    services: WebServices = SERVICES_DEPENDENCY,
) -> dict[str, object]:
    """Manually trigger a scheduled task immediately."""
    scheduler = services.task_scheduler
    task = scheduler.tasks.get(task_name)
    if task is None:
        raise HTTPException(status_code=404, detail=f"Task '{task_name}' not found")
    handle = services.task_queue.submit(
        scheduler._execute_task,
        task,
        name=f"schedule:{task_name}",
        metadata={"task_name": task_name, "trigger": "manual"},
    )
    return {"success": True, "task_id": handle.task_id, "status": "pending"}
