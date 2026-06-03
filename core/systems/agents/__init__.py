"""Agent runtime orchestration entrypoints (Layer 3).

This package contains the **runtime orchestration** side of subagents:
registries, runners, services, governance helpers, team orchestrators, etc.

The **declarative asset** side (capability profiles, middleware profiles,
storage, role policies, tool inventories, delegation payloads) lives under
``core.assets.agents`` (Layer 2). Import data classes from there.
"""

from __future__ import annotations

from importlib import import_module
from typing import Any

_EXPORTS: dict[str, tuple[str, str]] = {
    "build_subagent_governance_snapshot": (".subagent_governance", "build_subagent_governance_snapshot"),
    "list_sandbox_adapters": ("core.systems.agents.subagent_sandbox", "list_sandbox_adapters"),
    "AgentToolSyncError": (".agent_tool_sync", "AgentToolSyncError"),
    "sync_agent_tool": (".agent_tool_sync", "sync_agent_tool"),
    "create_sub_agent_instance": (".subagent_runtime", "create_sub_agent_instance"),
    "create_agent_record": (".agent_services", "create_agent_record"),
    "delegate_agent_task": (".agent_services", "delegate_agent_task"),
    "invoke_persisted_agent": (".agent_services", "invoke_persisted_agent"),
    "parse_capabilities": (".agent_services", "parse_capabilities"),
    "resume_persisted_agent_approval": (".agent_services", "resume_persisted_agent_approval"),
    "validate_agent_name": (".agent_services", "validate_agent_name"),
    "SubagentConcurrencyLimitError": (".subagent_registry", "SubagentConcurrencyLimitError"),
    "SubagentDepthLimitError": (".subagent_registry", "SubagentDepthLimitError"),
    "SubagentRegistry": (".subagent_registry", "SubagentRegistry"),
    "get_global_subagent_registry": (".subagent_registry", "get_global_subagent_registry"),
    "reset_global_subagent_registry": (".subagent_registry", "reset_global_subagent_registry"),
    "TaskContextSnapshot": (".task_snapshot", "TaskContextSnapshot"),
    "SnapshotRestoreReport": (".task_snapshot", "SnapshotRestoreReport"),
    "capture_snapshot": (".task_snapshot", "capture_snapshot"),
    "attach_snapshot": (".task_snapshot", "attach_snapshot"),
    "read_snapshot": (".task_snapshot", "read_snapshot"),
    "restore_for_resume": (".task_snapshot", "restore_for_resume"),
}

__all__ = sorted(_EXPORTS)


def __getattr__(name: str) -> Any:
    try:
        module_name, attr_name = _EXPORTS[name]
    except KeyError as exc:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}") from exc
    anchor = __name__ if module_name.startswith(".") else None
    module = import_module(module_name, anchor)
    return getattr(module, attr_name)


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(__all__))
