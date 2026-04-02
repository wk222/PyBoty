"""Public asset entrypoints for agent-related capabilities.

This package intentionally uses lazy exports so importing a leaf module like
``core.assets.agents.agent_capability_profile`` does not eagerly pull the whole
governance/runtime stack into memory and trigger circular imports.
"""

from __future__ import annotations

from importlib import import_module
from typing import Any

_EXPORTS: dict[str, tuple[str, str]] = {
    "AgentCapabilityProfile": (".governance", "AgentCapabilityProfile"),
    "AgentMiddlewareProfile": (".governance", "AgentMiddlewareProfile"),
    "build_subagent_governance_snapshot": (".governance", "build_subagent_governance_snapshot"),
    "list_capability_presets": (".governance", "list_capability_presets"),
    "list_middleware_presets": (".governance", "list_middleware_presets"),
    "list_sandbox_adapters": (".governance", "list_sandbox_adapters"),
    "AgentToolSyncError": (".inventory", "AgentToolSyncError"),
    "build_agent_tool_inventory": (".inventory", "build_agent_tool_inventory"),
    "sync_agent_tool": (".inventory", "sync_agent_tool"),
    "create_sub_agent_instance": (".runtime", "create_sub_agent_instance"),
    "create_agent_record": (".services", "create_agent_record"),
    "delegate_agent_task": (".services", "delegate_agent_task"),
    "invoke_persisted_agent": (".services", "invoke_persisted_agent"),
    "parse_capabilities": (".services", "parse_capabilities"),
    "resume_persisted_agent_approval": (".services", "resume_persisted_agent_approval"),
    "validate_agent_name": (".services", "validate_agent_name"),
    "AgentDefinition": (".storage", "AgentDefinition"),
    "AgentModelConfig": (".storage", "AgentModelConfig"),
    "AgentStorage": (".storage", "AgentStorage"),
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
    module = import_module(module_name, __name__)
    return getattr(module, attr_name)


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(__all__))
