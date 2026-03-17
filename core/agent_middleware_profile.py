"""Per-agent middleware stack profiles for subagent runtimes."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

_REQUIRED_SECTIONS = ("tool_control",)


@dataclass(frozen=True)
class AgentMiddlewareProfile:
    """Persisted middleware stack profile for a subagent."""

    preset: str = "default"
    sections: tuple[str, ...] = ("prompt_context", "policy_context", "tool_control")

    @classmethod
    def from_value(cls, value: Any = None) -> AgentMiddlewareProfile:
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
        raise ValueError("middleware_profile 必须是 JSON 对象、preset 名称或空值")

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> AgentMiddlewareProfile:
        preset = str(raw.get("preset", "default")).strip().lower() or "default"
        defaults = _preset_values(preset)
        requested_sections = raw.get("sections", defaults["sections"])
        return cls(
            preset=preset,
            sections=_normalize_sections(requested_sections),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "preset": self.preset,
            "sections": list(self.sections),
        }

    def stack_names(self) -> list[str]:
        return list(self.sections)

    def includes(self, section: str) -> bool:
        return section in self.sections


def _preset_values(preset: str) -> dict[str, Any]:
    if preset == "focused":
        return {"sections": ("prompt_context", "tool_control")}
    if preset == "reviewer":
        return {"sections": ("policy_context", "prompt_context", "tool_control")}
    if preset == "coordinator":
        return {"sections": ("prompt_context", "delegation_context", "policy_context", "tool_control")}
    if preset == "builder":
        return {"sections": ("prompt_context", "execution_context", "policy_context", "tool_control")}
    if preset == "locked_down":
        return {"sections": ("policy_context", "tool_control")}
    return {"sections": ("prompt_context", "policy_context", "tool_control")}


def list_middleware_presets() -> list[dict[str, Any]]:
    return [
        {
            "name": name,
            "description": description,
            "config": AgentMiddlewareProfile.from_dict({"preset": name}).to_dict(),
        }
        for name, description in (
            ("default", "Balanced runtime + policy context with enforced tool controls."),
            ("focused", "Minimal prompt context plus tool controls for narrow specialists."),
            ("reviewer", "Policy-heavy context for audit or review roles."),
            ("coordinator", "Delegation-oriented stack for planning and orchestration roles."),
            ("builder", "Execution-oriented stack for implementation and tool-building roles."),
            ("locked_down", "Only policy context plus tool controls for tightly governed agents."),
        )
    ]


def _normalize_sections(value: Any) -> tuple[str, ...]:
    if not value:
        requested: list[str] = []
    elif isinstance(value, str):
        requested = [value]
    else:
        requested = [str(item).strip().lower() for item in value]

    normalized: list[str] = []
    for section in requested:
        if section and section not in normalized:
            normalized.append(section)
    for required in _REQUIRED_SECTIONS:
        if required not in normalized:
            normalized.append(required)
    return tuple(normalized)
