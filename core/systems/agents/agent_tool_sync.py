"""Bidirectional sync helpers between agent-local and global tool libraries."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from core.assets.tools import ToolStorage

from core.assets.agents.storage import AgentStorage
from core.assets.agents.tool_inventory import build_agent_tool_inventory

SyncDirection = Literal["to_global", "from_global"]


@dataclass
class AgentToolSyncError(Exception):
    """Raised when an agent/local tool sync request cannot be completed."""

    message: str
    status_code: int = 400

    def __str__(self) -> str:
        return self.message


def sync_agent_tool(
    *,
    agent_storage: AgentStorage,
    global_tool_storage: ToolStorage,
    agent_name: str,
    tool_name: str,
    direction: SyncDirection,
    overwrite: bool = False,
) -> dict[str, Any]:
    """Synchronize a tool between the local agent library and the global tool library."""
    agent_def = agent_storage.get_agent(agent_name)
    if agent_def is None:
        raise AgentToolSyncError("Agent not found", status_code=404)

    local_tool_storage = ToolStorage(str(agent_storage.tools_dir_for(agent_name)))
    if direction == "to_global":
        source_label = "Local"
        target_label = "Global"
        source_storage = local_tool_storage
        target_storage = global_tool_storage
        created_action = "promoted_to_global"
        overwrite_action = "overwrote_global"
    else:
        source_label = "Global"
        target_label = "Local"
        source_storage = global_tool_storage
        target_storage = local_tool_storage
        created_action = "pulled_to_local"
        overwrite_action = "overwrote_local"

    source_definition = source_storage.get_tool(tool_name)
    if source_definition is None:
        raise AgentToolSyncError(f"{source_label} tool '{tool_name}' not found", status_code=404)

    target_definition = target_storage.get_tool(tool_name)
    if target_definition is None:
        target_storage.add_tool(tool_name, source_definition)
        action = created_action
    elif _tool_definitions_equal(target_definition, source_definition):
        action = "already_in_sync"
    elif overwrite:
        target_storage.upsert_tool(tool_name, source_definition)
        action = overwrite_action
    else:
        raise AgentToolSyncError(
            f"{target_label} tool '{tool_name}' already exists with different content",
            status_code=409,
        )

    return {
        "success": True,
        "agent_name": agent_name,
        "tool_name": tool_name,
        "direction": direction,
        "action": action,
        "tool_inventory": build_agent_tool_inventory(
            agent_def=agent_def,
            global_tool_storage=global_tool_storage,
            local_tool_storage=local_tool_storage,
        ),
        "local_tool": _tool_summary(local_tool_storage.get_tool(tool_name)),
        "global_tool": _tool_summary(global_tool_storage.get_tool(tool_name)),
    }


def _tool_summary(definition: dict[str, Any] | None) -> dict[str, str] | None:
    if definition is None:
        return None
    return {
        "name": str(definition.get("name", "")),
        "description": str(definition.get("description", "")),
        "usage_guide": str(definition.get("usage_guide", "")),
    }


def _tool_definitions_equal(left: dict[str, Any], right: dict[str, Any]) -> bool:
    comparable_keys = ("name", "description", "parameters", "code", "dependencies", "usage_guide", "from_template")
    return {key: left.get(key) for key in comparable_keys} == {key: right.get(key) for key in comparable_keys}
