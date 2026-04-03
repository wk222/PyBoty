"""Public asset entrypoints for agent-related capabilities.

This package intentionally uses lazy exports so importing a leaf module like
``core.assets.agents.agent_capability_profile`` does not eagerly pull the whole
governance/runtime stack into memory and trigger circular imports.
"""

from __future__ import annotations

from importlib import import_module
from typing import Any

_EXPORTS: dict[str, tuple[str, str]] = {
    "AgentCapabilityProfile": (".agent_capability_profile", "AgentCapabilityProfile"),
    "AgentMiddlewareProfile": (".agent_middleware_profile", "AgentMiddlewareProfile"),
    "build_subagent_governance_snapshot": (".subagent_governance", "build_subagent_governance_snapshot"),
    "list_capability_presets": (".agent_capability_profile", "list_capability_presets"),
    "list_middleware_presets": (".agent_middleware_profile", "list_middleware_presets"),
    "list_sandbox_adapters": ("core.systems.governance.subagent_sandbox", "list_sandbox_adapters"),
    "AgentToolSyncError": (".agent_tool_sync", "AgentToolSyncError"),
    "build_agent_tool_inventory": (".agent_tool_inventory", "build_agent_tool_inventory"),
    "sync_agent_tool": (".agent_tool_sync", "sync_agent_tool"),
    "create_sub_agent_instance": (".subagent_runtime", "create_sub_agent_instance"),
    "create_agent_record": (".agent_services", "create_agent_record"),
    "delegate_agent_task": (".agent_services", "delegate_agent_task"),
    "invoke_persisted_agent": (".agent_services", "invoke_persisted_agent"),
    "parse_capabilities": (".agent_services", "parse_capabilities"),
    "resume_persisted_agent_approval": (".agent_services", "resume_persisted_agent_approval"),
    "validate_agent_name": (".agent_services", "validate_agent_name"),
    "AgentDefinition": (".agent_storage", "AgentDefinition"),
    "AgentModelConfig": (".agent_storage", "AgentModelConfig"),
    "AgentStorage": (".agent_storage", "AgentStorage"),
    "SubagentConcurrencyLimitError": (".subagent_registry", "SubagentConcurrencyLimitError"),
    "SubagentDepthLimitError": (".subagent_registry", "SubagentDepthLimitError"),
    "SubagentRegistry": (".subagent_registry", "SubagentRegistry"),
    "get_global_subagent_registry": (".subagent_registry", "get_global_subagent_registry"),
    "reset_global_subagent_registry": (".subagent_registry", "reset_global_subagent_registry"),
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
