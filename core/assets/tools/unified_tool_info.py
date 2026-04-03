"""Flat, source-agnostic descriptor for any tool in the PyBot inventory."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any


LAYER_TOOL = "tool"
LAYER_SKILL_TOOL = "skill_tool"


@dataclass
class UnifiedToolInfo:
    """Single view of a tool regardless of whether it lives in ToolStorage or a Skill bundle.

    ``source`` encodes the origin:
    - ``"global"``         — direct entry in the global ToolStorage
    - ``"skill:<name>"``   — part of a Skill bundle
    - ``"agent:<name>"``   — per-agent local tool
    """

    name: str
    description: str
    parameters: list[dict[str, Any]] = field(default_factory=list)
    dependencies: list[str] = field(default_factory=list)

    layer: str = LAYER_TOOL
    source: str = "global"
    skill_name: str | None = None
    agent_name: str | None = None

    tags: list[str] = field(default_factory=list)
    enabled: bool = True
    user_invocable: bool = True
    usage_guide: str = ""
    system_prompt_extension: str = ""

    usage_count: int = 0
    created_at: float = field(default_factory=time.time)
    metadata: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_tool_def(cls, name: str, tool_def: dict[str, Any]) -> "UnifiedToolInfo":
        """Build from a raw ToolStorage entry."""
        return cls(
            name=name,
            description=str(tool_def.get("description", "")),
            parameters=list(tool_def.get("parameters", [])),
            dependencies=list(tool_def.get("dependencies", [])),
            layer=LAYER_TOOL,
            source="global",
            tags=list(tool_def.get("tags", [])),
            enabled=True,
            user_invocable=True,
            usage_guide=str(tool_def.get("usage_guide", "")),
            usage_count=int(tool_def.get("usage_count", 0)),
            metadata={k: v for k, v in tool_def.items() if k not in _TOOL_DEF_CORE_KEYS},
        )

    @classmethod
    def from_skill_tool_def(
        cls,
        tool_def: dict[str, Any],
        *,
        skill_name: str,
        skill_enabled: bool = True,
        skill_tags: list[str] | None = None,
        system_prompt_extension: str = "",
    ) -> "UnifiedToolInfo":
        """Build from a tool definition embedded inside a SkillDefinition."""
        name = str(tool_def.get("name", ""))
        return cls(
            name=name,
            description=str(tool_def.get("description", "")),
            parameters=list(tool_def.get("parameters", [])),
            dependencies=list(tool_def.get("dependencies", [])),
            layer=LAYER_SKILL_TOOL,
            source=f"skill:{skill_name}",
            skill_name=skill_name,
            tags=list(skill_tags or []),
            enabled=skill_enabled,
            user_invocable=True,
            usage_guide=str(tool_def.get("usage_guide", "")),
            system_prompt_extension=system_prompt_extension,
            metadata={k: v for k, v in tool_def.items() if k not in _TOOL_DEF_CORE_KEYS},
        )

    def to_summary(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "layer": self.layer,
            "source": self.source,
            "enabled": self.enabled,
            "tags": self.tags,
            "usage_count": self.usage_count,
        }


_TOOL_DEF_CORE_KEYS = frozenset(
    {"name", "description", "parameters", "code", "dependencies", "usage_guide", "usage_count", "tags"}
)
