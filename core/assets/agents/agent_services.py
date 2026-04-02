"""Service-layer helpers for persisted subagent creation and delegation."""

from __future__ import annotations

import json
import os
import time
from collections.abc import Callable
from typing import Any

from langchain_core.language_models import BaseChatModel

from core.assets.tools.tool_storage import ToolStorage
from core.systems.governance.agent_control import AgentControlPolicy
from core.systems.governance.approval_queue import ApprovalQueue
from core.systems.runtime.project_paths import ProjectPaths

from .agent_capability_profile import AgentCapabilityProfile
from .agent_middleware_profile import AgentMiddlewareProfile
from .agent_storage import AgentDefinition, AgentModelConfig, AgentStorage
from .agent_tool_inventory import build_agent_tool_inventory
from .delegation_payload import normalize_delegation_payload
from .subagent_governance import build_delegation_chain, build_subagent_governance_snapshot, format_delegation_tree
from .subagent_registry import SubagentRegistry
from .subagent_runtime import create_sub_agent_instance


def validate_agent_name(agent_name: str) -> bool:
    return bool(agent_name) and agent_name.replace("_", "").isalnum()


def parse_capabilities(capabilities: str | list[str] | None) -> list[str]:
    if capabilities is None:
        return []
    if isinstance(capabilities, list):
        return [str(item) for item in capabilities if str(item).strip()]
    try:
        parsed = json.loads(capabilities)
    except Exception:
        return []
    if not isinstance(parsed, list):
        return []
    return [str(item) for item in parsed if str(item).strip()]


def create_agent_record(
    *,
    agent_storage: AgentStorage,
    agent_name: str,
    role: str,
    description: str,
    system_prompt: str,
    capabilities: str | list[str] | None = None,
    capability_profile: str | dict[str, Any] | None = None,
    middleware_profile: str | dict[str, Any] | None = None,
    model: str = "gemini-3-flash-preview",
    temperature: float = 0.7,
) -> dict[str, Any]:
    """Create and persist a new subagent definition."""
    if not validate_agent_name(agent_name):
        return {
            "success": False,
            "error": "智能体名称只能包含字母、数字和下划线",
        }

    caps = parse_capabilities(capabilities)
    profile = AgentCapabilityProfile.from_value(capability_profile)
    middleware = AgentMiddlewareProfile.from_value(middleware_profile)
    agent_def = AgentDefinition(
        name=agent_name,
        role=role,
        description=description,
        system_prompt=system_prompt,
        tools=[],
        model_config_data=AgentModelConfig(model_id=model, temperature=temperature),
        capabilities=caps,
        capability_profile=profile.to_dict(),
        middleware_profile=middleware.to_dict(),
        created_at=time.time(),
        usage_count=0,
        enabled=True,
    )
    if not agent_storage.add_agent(agent_def):
        return {
            "success": False,
            "error": f"智能体 '{agent_name}' 已存在",
        }

    agent_dir = os.path.join(agent_storage.base_dir, agent_name)
    return {
        "success": True,
        "agent_name": agent_name,
        "message": f"✅ 智能体 '{agent_name}' 创建成功！",
        "usage": f"现在可以使用 delegate_to_agent 工具将任务委派给 {agent_name}",
        "details": {
            "role": role,
            "description": description,
            "capabilities": caps,
            "capability_profile": profile.to_dict(),
            "middleware_profile": middleware.to_dict(),
            "directory": agent_dir,
            "tools_dir": os.path.join(agent_dir, "tools"),
        },
    }


def invoke_persisted_agent(
    *,
    agent_storage: AgentStorage,
    llm_factory: Callable[[str | None, float | None], BaseChatModel],
    agent_name: str,
    task: str,
    context: str = "",
    thread_id: str | None = None,
    parent_state: dict[str, Any] | None = None,
    control_policy: AgentControlPolicy | None = None,
    global_tool_storage: ToolStorage | None = None,
    approval_queue: ApprovalQueue | None = None,
    project_paths: ProjectPaths | None = None,
    subagent_registry: SubagentRegistry | None = None,
    parent_agent_name: str | None = "root",
    parent_run_id: str | None = None,
    parent_thread_id: str | None = None,
    parent_depth: int = 0,
    stream: bool = False,
) -> dict[str, Any] | Any:
    """Invoke a persisted subagent and return its structured runtime payload."""
    if control_policy is not None and not control_policy.allow_agent_delegation:
        raise ValueError("当前 agent control 策略禁止子智能体委派")

    agent_def = agent_storage.get_agent(agent_name)
    if not agent_def:
        raise ValueError(f"子智能体 '{agent_name}' 不存在")
    if not agent_def.enabled:
        raise ValueError(f"子智能体 '{agent_name}' 已被禁用")

    agent_storage.increment_usage(agent_name)
    agent_tools_dir = os.path.join(agent_storage.base_dir, agent_name, "tools")
    tool_storage = ToolStorage(base_dir=agent_tools_dir)
    runtime = create_sub_agent_instance(
        agent_def=agent_def,
        tool_storage=tool_storage,
        global_tool_storage=global_tool_storage,
        agent_storage=agent_storage,
        llm_factory=llm_factory,
        control_policy=control_policy,
        approval_queue=approval_queue,
        project_paths=project_paths,
        registry=subagent_registry,
    )
    resolved_thread_id = thread_id or f"delegate_{agent_name}_{int(time.time())}"
    try:
        result = runtime.invoke(
            task=task,
            context=context,
            thread_id=resolved_thread_id,
            parent_state=parent_state,
            parent_agent_name=parent_agent_name,
            parent_run_id=parent_run_id,
            parent_thread_id=parent_thread_id,
            parent_depth=parent_depth,
            stream=stream,
        )
    finally:
        if not stream:
            close_runtime = getattr(runtime, "close", None)
            if callable(close_runtime):
                close_runtime()
    
    if stream:
        return result

    current_agent = agent_storage.get_agent(agent_name) or agent_def
    current_capability_profile = AgentCapabilityProfile.from_value(current_agent.capability_profile)
    current_middleware_profile = AgentMiddlewareProfile.from_value(current_agent.middleware_profile)
    tool_inventory = build_agent_tool_inventory(
        agent_def=current_agent,
        global_tool_storage=global_tool_storage,
        local_tool_storage=tool_storage,
    )
    return {
        **result,
        "task": task,
        "usage_count": current_agent.usage_count,
        "has_state_update": bool(result.get("state_update")),
        "state_keys": sorted((result.get("state_update") or {}).keys()),
        "assigned_tools": list(current_agent.tools),
        "tool_inventory": tool_inventory,
        "capability_profile": current_capability_profile.to_dict(),
        "middleware_profile": current_middleware_profile.to_dict(),
        "governance": build_subagent_governance_snapshot(
            base_policy=control_policy,
            capability_profile=current_capability_profile,
            middleware_profile=current_middleware_profile,
        ),
        "sandbox": result.get("sandbox", {}),
    }


def delegate_agent_task(
    *,
    agent_storage: AgentStorage,
    llm_factory: Callable[[str | None, float | None], BaseChatModel],
    agent_name: str,
    task: str,
    context: str = "",
    control_policy: AgentControlPolicy | None = None,
    global_tool_storage: ToolStorage | None = None,
    approval_queue: ApprovalQueue | None = None,
    project_paths: ProjectPaths | None = None,
    subagent_registry: SubagentRegistry | None = None,
    parent_agent_name: str | None = "root",
    parent_run_id: str | None = None,
    parent_thread_id: str | None = None,
    parent_depth: int = 0,
    stream: bool = False,
) -> dict[str, Any] | Any:
    """Invoke a persisted subagent and return a parent-agent friendly payload."""
    result = invoke_persisted_agent(
        agent_storage=agent_storage,
        llm_factory=llm_factory,
        agent_name=agent_name,
        task=task,
        context=context,
        control_policy=control_policy,
        global_tool_storage=global_tool_storage,
        approval_queue=approval_queue,
        project_paths=project_paths,
        subagent_registry=subagent_registry,
        parent_agent_name=parent_agent_name,
        parent_run_id=parent_run_id,
        parent_thread_id=parent_thread_id,
        parent_depth=parent_depth,
        stream=stream,
    )
    if stream:
        return result
        
    payload = normalize_delegation_payload(result, agent_name=agent_name, task=task)
    payload["usage_count"] = result.get("usage_count", 0)
    payload["capability_profile"] = result.get("capability_profile", {})
    payload["middleware_profile"] = result.get("middleware_profile", {})
    payload["governance"] = result.get("governance", {})
    return payload


def resume_persisted_agent_approval(
    *,
    agent_storage: AgentStorage,
    llm_factory: Callable[[str | None, float | None], BaseChatModel],
    approval_queue: ApprovalQueue,
    approval_id: str,
    agent_name: str,
    thread_id: str,
    approved: bool,
    note: str = "",
    control_policy: AgentControlPolicy | None = None,
    global_tool_storage: ToolStorage | None = None,
    project_paths: ProjectPaths | None = None,
    subagent_registry: SubagentRegistry | None = None,
) -> dict[str, Any]:
    """Rebuild a persisted subagent runtime and resume a stored tool approval."""
    agent_def = agent_storage.get_agent(agent_name)
    if not agent_def:
        raise ValueError(f"子智能体 '{agent_name}' 不存在")

    agent_tools_dir = os.path.join(agent_storage.base_dir, agent_name, "tools")
    tool_storage = ToolStorage(base_dir=agent_tools_dir)
    runtime = create_sub_agent_instance(
        agent_def=agent_def,
        tool_storage=tool_storage,
        global_tool_storage=global_tool_storage,
        agent_storage=agent_storage,
        llm_factory=llm_factory,
        control_policy=control_policy,
        approval_queue=approval_queue,
        project_paths=project_paths,
        registry=subagent_registry,
    )
    try:
        result = runtime.resume_approval(
            approval_id=approval_id,
            thread_id=thread_id,
            approved=approved,
            note=note,
        )
    finally:
        close_runtime = getattr(runtime, "close", None)
        if callable(close_runtime):
            close_runtime()
    approval_queue.set_resolution_result(approval_id, result)
    return result


def list_agent_delegation_chain(
    agent_storage: AgentStorage,
    root_policy: AgentControlPolicy | None = None,
) -> dict[str, Any]:
    """Build the full multi-level delegation chain for all stored agents.

    Returns both a structured chain and a formatted ASCII tree.
    """
    agents_data: list[dict[str, Any]] = []
    for agent_def in agent_storage.list_agents():
        cap = AgentCapabilityProfile.from_value(agent_def.capability_profile)
        agents_data.append(
            {
                "name": agent_def.name,
                "role": agent_def.role,
                "parent": agent_def.metadata.get("parent_agent") if agent_def.metadata else None,
                "capability_profile": cap,
            }
        )
    chain = build_delegation_chain(agents_data, root_policy=root_policy)
    tree = format_delegation_tree(chain)
    return {
        "chain": chain,
        "tree": tree,
        "agent_count": len(agents_data),
    }
