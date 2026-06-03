"""Persisted agent asset definitions.

This subpackage holds the **declarative, serializable** parts of subagents
(capability profiles, middleware profiles, role policies, storage, tool
inventories, delegation payloads). It is a Layer 2 (asset domain) sibling of
``core.assets.tools`` / ``core.assets.skills`` / ``core.assets.workflows``.

The runtime orchestration counterparts (subagent registry, persistent agent
runner, team orchestrator, agent creator, etc.) live under
``core.systems.agents`` and belong to Layer 3 (product modes).
"""

from __future__ import annotations

from core.assets.agents.capability_profile import (
    AgentCapabilityProfile,
    list_capability_presets,
)
from core.assets.agents.delegation_payload import normalize_delegation_payload
from core.assets.agents.middleware_profile import (
    AgentMiddlewareProfile,
    list_middleware_presets,
)
from core.assets.agents.role_policy import (
    ROLE_POLICIES,
    AgentRole,
    AgentRolePolicy,
    apply_role_defaults,
    get_policy,
)
from core.assets.agents.storage import (
    AgentDefinition,
    AgentModelConfig,
    AgentStorage,
)
from core.assets.agents.tool_inventory import build_agent_tool_inventory

__all__ = [
    "ROLE_POLICIES",
    "AgentCapabilityProfile",
    "AgentDefinition",
    "AgentMiddlewareProfile",
    "AgentModelConfig",
    "AgentRole",
    "AgentRolePolicy",
    "AgentStorage",
    "apply_role_defaults",
    "build_agent_tool_inventory",
    "get_policy",
    "list_capability_presets",
    "list_middleware_presets",
    "normalize_delegation_payload",
]
