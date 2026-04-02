"""Structured inventory helpers for agent-assigned and local tools."""

from __future__ import annotations

from typing import Any

from core.assets.tools.tool_storage import ToolStorage

from .agent_storage import AgentDefinition


def build_agent_tool_inventory(
    *,
    agent_def: AgentDefinition,
    global_tool_storage: ToolStorage | None = None,
    local_tool_storage: ToolStorage | None = None,
) -> dict[str, Any]:
    """Return a normalized inventory separating global assignments from local tools."""
    assigned_global_tools: list[dict[str, Any]] = []
    missing_assigned_tools: list[dict[str, Any]] = []
    local_tools: list[dict[str, Any]] = []
    local_tool_definitions: dict[str, dict[str, Any]] = {}

    if local_tool_storage is not None:
        for tool_name in sorted(local_tool_storage.list_tools().keys()):
            definition = local_tool_storage.get_tool(tool_name) or {}
            local_tool_definitions[tool_name] = definition

    for tool_name in agent_def.tools:
        definition = global_tool_storage.get_tool(tool_name) if global_tool_storage is not None else None
        local_definition = local_tool_definitions.get(tool_name)
        entry = {
            "name": tool_name,
            "description": _definition_value(definition, "description"),
            "usage_guide": _definition_value(definition, "usage_guide"),
            "source": "assigned_global",
            "exists": definition is not None,
            "available_locally": local_definition is not None,
            "sync_status": _global_tool_sync_status(definition, local_definition),
        }
        if definition is None:
            missing_assigned_tools.append(entry)
        else:
            assigned_global_tools.append(entry)

    if local_tool_storage is not None:
        for tool_name, definition in local_tool_definitions.items():
            global_definition = global_tool_storage.get_tool(tool_name) if global_tool_storage is not None else None
            local_tools.append(
                {
                    "name": tool_name,
                    "description": str(definition.get("description", "")),
                    "usage_guide": str(definition.get("usage_guide", "")),
                    "source": "local_dynamic",
                    "exists": True,
                    "available_globally": global_definition is not None,
                    "sync_status": _local_tool_sync_status(definition, global_definition),
                }
            )

    return {
        "assigned_global_tools": assigned_global_tools,
        "local_tools": local_tools,
        "missing_assigned_tools": missing_assigned_tools,
        "assigned_global_tool_names": [tool["name"] for tool in assigned_global_tools],
        "local_tool_names": [tool["name"] for tool in local_tools],
        "counts": {
            "assigned_global": len(assigned_global_tools),
            "local": len(local_tools),
            "missing_assigned": len(missing_assigned_tools),
        },
    }


def _definition_value(definition: dict[str, Any] | None, key: str) -> str:
    if definition is None:
        return ""
    return str(definition.get(key, ""))


def _local_tool_sync_status(
    local_definition: dict[str, Any],
    global_definition: dict[str, Any] | None,
) -> str:
    if global_definition is None:
        return "local_only"
    if _tool_definitions_equal(local_definition, global_definition):
        return "in_sync"
    return "conflict"


def _global_tool_sync_status(
    global_definition: dict[str, Any] | None,
    local_definition: dict[str, Any] | None,
) -> str:
    if global_definition is None:
        return "missing"
    if local_definition is None:
        return "global_only"
    if _tool_definitions_equal(global_definition, local_definition):
        return "in_sync"
    return "conflict"


def _tool_definitions_equal(left: dict[str, Any], right: dict[str, Any]) -> bool:
    comparable_keys = ("name", "description", "parameters", "code", "dependencies", "usage_guide", "from_template")
    return {key: left.get(key) for key in comparable_keys} == {key: right.get(key) for key in comparable_keys}
