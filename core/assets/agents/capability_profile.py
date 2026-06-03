"""Capability profiles for persisted subagents."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class AgentCapabilityProfile:
    """Control which privileged capabilities a subagent may receive."""

    preset: str = "specialist"
    control_mode: str = "inherit"
    sandbox_mode: str = "restricted"
    sandbox_adapter: str = "auto"
    allow_local_dynamic_tools: bool = True
    allow_local_tool_creation: bool = False
    allow_local_tool_removal: bool = False
    allow_template_tools: bool = False
    allow_agent_creation: bool = False
    allow_agent_removal: bool = False
    allow_agent_delegation: bool = False
    allow_list_agents: bool = False
    allow_code_execution: bool = False
    allow_workflow_management: bool = False
    allow_skill_installation: bool = False
    allow_app_mutation: bool = False
    allow_memory_garden: bool = False
    allow_dense_memory: bool = False
    blocked_tools: tuple[str, ...] = ()
    approval_required_tools: tuple[str, ...] = ()

    @classmethod
    def from_value(cls, value: Any = None) -> AgentCapabilityProfile:
        if isinstance(value, cls):
            return value
        if value is None or value == "":
            return cls.from_dict({})
        if isinstance(value, str):
            text = value.strip()
            if not text:
                return cls.from_dict({})
            try:
                parsed = json.loads(text)
            except json.JSONDecodeError:
                return cls.from_dict({"preset": text})
            return cls.from_dict(parsed)
        if isinstance(value, dict):
            return cls.from_dict(value)
        raise ValueError("capability_profile 必须是 JSON 对象、preset 名称或空值")

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> AgentCapabilityProfile:
        preset = str(raw.get("preset", "specialist")).strip().lower() or "specialist"
        defaults = _preset_values(preset)
        data = {**defaults, **raw, "preset": preset}
        return cls(
            preset=preset,
            control_mode=str(data.get("control_mode", "inherit")).strip().lower() or "inherit",
            sandbox_mode=str(data.get("sandbox_mode", "restricted")).strip().lower() or "restricted",
            sandbox_adapter=str(data.get("sandbox_adapter", "auto")).strip().lower() or "auto",
            allow_local_dynamic_tools=bool(data.get("allow_local_dynamic_tools", True)),
            allow_local_tool_creation=bool(data.get("allow_local_tool_creation", False)),
            allow_local_tool_removal=bool(data.get("allow_local_tool_removal", False)),
            allow_template_tools=bool(data.get("allow_template_tools", False)),
            allow_agent_creation=bool(data.get("allow_agent_creation", False)),
            allow_agent_removal=bool(data.get("allow_agent_removal", False)),
            allow_agent_delegation=bool(data.get("allow_agent_delegation", False)),
            allow_list_agents=bool(data.get("allow_list_agents", False)),
            allow_code_execution=bool(data.get("allow_code_execution", False)),
            allow_workflow_management=bool(data.get("allow_workflow_management", False)),
            allow_skill_installation=bool(data.get("allow_skill_installation", False)),
            allow_app_mutation=bool(data.get("allow_app_mutation", False)),
            allow_memory_garden=bool(data.get("allow_memory_garden", False)),
            allow_dense_memory=bool(data.get("allow_dense_memory", False)),
            blocked_tools=_normalize_names(data.get("blocked_tools")),
            approval_required_tools=_normalize_names(data.get("approval_required_tools")),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "preset": self.preset,
            "control_mode": self.control_mode,
            "sandbox_mode": self.sandbox_mode,
            "sandbox_adapter": self.sandbox_adapter,
            "allow_local_dynamic_tools": self.allow_local_dynamic_tools,
            "allow_local_tool_creation": self.allow_local_tool_creation,
            "allow_local_tool_removal": self.allow_local_tool_removal,
            "allow_template_tools": self.allow_template_tools,
            "allow_agent_creation": self.allow_agent_creation,
            "allow_agent_removal": self.allow_agent_removal,
            "allow_agent_delegation": self.allow_agent_delegation,
            "allow_list_agents": self.allow_list_agents,
            "allow_code_execution": self.allow_code_execution,
            "allow_workflow_management": self.allow_workflow_management,
            "allow_skill_installation": self.allow_skill_installation,
            "allow_app_mutation": self.allow_app_mutation,
            "allow_memory_garden": self.allow_memory_garden,
            "allow_dense_memory": self.allow_dense_memory,
            "blocked_tools": list(self.blocked_tools),
            "approval_required_tools": list(self.approval_required_tools),
        }

    def grants_privileged_capabilities(self) -> bool:
        return any(
            (
                self.allow_local_tool_creation,
                self.allow_local_tool_removal,
                self.allow_template_tools,
                self.allow_agent_creation,
                self.allow_agent_removal,
                self.allow_agent_delegation,
                self.allow_code_execution,
                self.allow_workflow_management,
                self.allow_skill_installation,
                self.allow_app_mutation,
                self.allow_memory_garden,
                self.allow_dense_memory,
            )
        )


def _preset_values(preset: str) -> dict[str, Any]:
    if preset == "researcher":
        return {
            "control_mode": "inherit",
            "sandbox_mode": "read_only",
            "sandbox_adapter": "workspace",
            "allow_local_dynamic_tools": False,
            "allow_local_tool_creation": False,
            "allow_local_tool_removal": False,
            "allow_template_tools": False,
            "allow_agent_creation": False,
            "allow_agent_removal": False,
            "allow_agent_delegation": False,
            "allow_list_agents": True,
            "allow_code_execution": False,
            "allow_workflow_management": False,
            "allow_skill_installation": False,
            "allow_app_mutation": False,
            "allow_memory_garden": True,
            "allow_dense_memory": False,
            "blocked_tools": (),
            "approval_required_tools": (),
        }
    if preset == "reviewer":
        return {
            "control_mode": "strict",
            "sandbox_mode": "read_only",
            "sandbox_adapter": "workspace",
            "allow_local_dynamic_tools": False,
            "allow_local_tool_creation": False,
            "allow_local_tool_removal": False,
            "allow_template_tools": False,
            "allow_agent_creation": False,
            "allow_agent_removal": False,
            "allow_agent_delegation": False,
            "allow_list_agents": True,
            "allow_code_execution": False,
            "allow_workflow_management": False,
            "allow_skill_installation": False,
            "allow_app_mutation": False,
            "allow_memory_garden": True,
            "allow_dense_memory": False,
            "blocked_tools": (),
            "approval_required_tools": (),
        }
    if preset == "builder":
        return {
            "control_mode": "inherit",
            "sandbox_mode": "restricted",
            "sandbox_adapter": "isolated",
            "allow_local_dynamic_tools": True,
            "allow_local_tool_creation": True,
            "allow_local_tool_removal": True,
            "allow_template_tools": True,
            "allow_agent_creation": False,
            "allow_agent_removal": False,
            "allow_agent_delegation": False,
            "allow_list_agents": False,
            "allow_code_execution": True,
            "allow_workflow_management": False,
            "allow_skill_installation": False,
            "allow_app_mutation": False,
            "allow_memory_garden": True,
            "allow_dense_memory": False,
            "blocked_tools": (),
            "approval_required_tools": (),
        }
    if preset == "coordinator":
        return {
            "control_mode": "balanced",
            "sandbox_mode": "restricted",
            "sandbox_adapter": "shared_tools",
            "allow_local_dynamic_tools": True,
            "allow_local_tool_creation": False,
            "allow_local_tool_removal": False,
            "allow_template_tools": False,
            "allow_agent_creation": False,
            "allow_agent_removal": False,
            "allow_agent_delegation": True,
            "allow_list_agents": True,
            "allow_code_execution": False,
            "allow_workflow_management": True,
            "allow_skill_installation": False,
            "allow_app_mutation": False,
            "allow_memory_garden": True,
            "allow_dense_memory": False,
            "blocked_tools": (),
            "approval_required_tools": (),
        }
    if preset == "operator":
        return {
            "control_mode": "balanced",
            "sandbox_mode": "restricted",
            "sandbox_adapter": "shared_tools",
            "allow_local_dynamic_tools": True,
            "allow_local_tool_creation": False,
            "allow_local_tool_removal": False,
            "allow_template_tools": False,
            "allow_agent_creation": False,
            "allow_agent_removal": False,
            "allow_agent_delegation": False,
            "allow_list_agents": True,
            "allow_code_execution": True,
            "allow_workflow_management": True,
            "allow_skill_installation": False,
            "allow_app_mutation": False,
            "allow_memory_garden": False,
            "allow_dense_memory": False,
            "blocked_tools": (),
            "approval_required_tools": (),
        }
    if preset == "manager":
        return {
            "control_mode": "balanced",
            "sandbox_mode": "restricted",
            "sandbox_adapter": "shared_tools",
            "allow_local_dynamic_tools": True,
            "allow_local_tool_creation": False,
            "allow_local_tool_removal": False,
            "allow_template_tools": False,
            "allow_agent_creation": True,
            "allow_agent_removal": True,
            "allow_agent_delegation": True,
            "allow_list_agents": True,
            "allow_code_execution": False,
            "allow_workflow_management": True,
            "allow_skill_installation": False,
            "allow_app_mutation": True,
            "allow_memory_garden": True,
            "allow_dense_memory": False,
            "blocked_tools": (),
            "approval_required_tools": (),
        }
    if preset == "maintainer":
        return {
            "control_mode": "balanced",
            "sandbox_mode": "workspace_write",
            "sandbox_adapter": "workspace",
            "allow_local_dynamic_tools": True,
            "allow_local_tool_creation": True,
            "allow_local_tool_removal": True,
            "allow_template_tools": True,
            "allow_agent_creation": False,
            "allow_agent_removal": False,
            "allow_agent_delegation": True,
            "allow_list_agents": True,
            "allow_code_execution": True,
            "allow_workflow_management": True,
            "allow_skill_installation": True,
            "allow_app_mutation": True,
            "allow_memory_garden": True,
            "allow_dense_memory": False,
            "blocked_tools": (),
            "approval_required_tools": (),
        }
    if preset == "lead":
        return {
            "control_mode": "balanced",
            "sandbox_mode": "workspace_write",
            "sandbox_adapter": "workspace",
            "allow_local_dynamic_tools": True,
            "allow_local_tool_creation": True,
            "allow_local_tool_removal": True,
            "allow_template_tools": True,
            "allow_agent_creation": True,
            "allow_agent_removal": True,
            "allow_agent_delegation": True,
            "allow_list_agents": True,
            "allow_code_execution": True,
            "allow_workflow_management": True,
            "allow_skill_installation": True,
            "allow_app_mutation": True,
            "allow_memory_garden": True,
            "allow_dense_memory": False,
            "blocked_tools": (),
            "approval_required_tools": (),
        }
    return {
        "control_mode": "inherit",
        "sandbox_mode": "restricted",
        "sandbox_adapter": "auto",
        "allow_local_dynamic_tools": True,
        "allow_local_tool_creation": False,
        "allow_local_tool_removal": False,
        "allow_template_tools": False,
        "allow_agent_creation": False,
        "allow_agent_removal": False,
        "allow_agent_delegation": False,
        "allow_list_agents": False,
        "allow_code_execution": False,
        "allow_workflow_management": False,
        "allow_skill_installation": False,
        "allow_app_mutation": False,
        "allow_memory_garden": False,
        "allow_dense_memory": False,
        "blocked_tools": (),
        "approval_required_tools": (),
    }


def list_capability_presets() -> list[dict[str, Any]]:
    return [
        {
            "name": name,
            "description": description,
            "config": AgentCapabilityProfile.from_dict({"preset": name}).to_dict(),
        }
        for name, description in (
            ("specialist", "Default focused worker with restricted isolated sandbox and no privileged mutations."),
            ("researcher", "Read-only project reader for investigation, search, and synthesis tasks."),
            ("reviewer", "Strict read-only reviewer that can inspect but not mutate tools, agents, or apps."),
            ("builder", "Isolated implementation worker allowed to create local tools and run code."),
            ("coordinator", "Delegating coordinator that can route work and manage workflows without local code exec."),
            (
                "operator",
                "Shared-tools operator that can execute code and workflows outside the main project workspace.",
            ),
            ("manager", "Balanced manager that can delegate and mutate agents/apps without code execution."),
            (
                "maintainer",
                "Workspace-writing maintainer for operational changes without full team-level mutation powers.",
            ),
            ("lead", "Broadly empowered lead profile with project-wide writes, execution, delegation, and mutations."),
        )
    ]


def _normalize_names(value: Any) -> tuple[str, ...]:
    if not value:
        return ()
    if isinstance(value, str):
        item = value.strip()
        return (item,) if item else ()
    names: list[str] = []
    for item in value:
        item_str = str(item).strip()
        if item_str and item_str not in names:
            names.append(item_str)
    return tuple(names)
