"""Configurable capability profiles for the three root modes."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from core.system_model import get_root_mode_label, normalize_root_mode

_CAPABILITY_LABELS = {
    "interactive_chat": "交互对话",
    "durable_goal_loop": "长期任务循环",
    "app_orchestration": "APP 编排运行时",
    "app_topology_planning": "APP 拓扑规划",
}


@dataclass(frozen=True)
class ModeProfile:
    """Mode profile treated as a capability bundle for the root runtime."""

    name: str
    label: str
    attach_admin_runtime_by_default: bool
    enables_interactive_chat: bool
    enables_durable_goal_loop: bool
    enables_app_orchestration: bool
    enables_app_topology_planning: bool
    durable_runtime_dir: str

    def capability_flags(self) -> dict[str, bool]:
        return {
            "interactive_chat": self.enables_interactive_chat,
            "durable_goal_loop": self.enables_durable_goal_loop,
            "app_orchestration": self.enables_app_orchestration,
            "app_topology_planning": self.enables_app_topology_planning,
        }

    def enabled_capabilities(self) -> list[str]:
        return [name for name, enabled in self.capability_flags().items() if enabled]

    def supports(self, capability_name: str) -> bool:
        return self.capability_flags().get(capability_name, False)

    def capability_lines(self) -> list[str]:
        return [
            f"- {_CAPABILITY_LABELS[name]}: {'启用' if enabled else '禁用'}"
            for name, enabled in self.capability_flags().items()
        ]

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "label": self.label,
            "attach_admin_runtime_by_default": self.attach_admin_runtime_by_default,
            "durable_runtime_dir": self.durable_runtime_dir,
            "capabilities": self.capability_flags(),
            "enabled_capabilities": self.enabled_capabilities(),
        }


_MODE_PROFILES: dict[str, ModeProfile] = {
    "assistant": ModeProfile(
        name="assistant",
        label=get_root_mode_label("assistant"),
        attach_admin_runtime_by_default=False,
        enables_interactive_chat=True,
        enables_durable_goal_loop=False,
        enables_app_orchestration=False,
        enables_app_topology_planning=False,
        durable_runtime_dir="assistant",
    ),
    "app_matrix": ModeProfile(
        name="app_matrix",
        label=get_root_mode_label("app_matrix"),
        attach_admin_runtime_by_default=True,
        enables_interactive_chat=True,
        enables_durable_goal_loop=True,
        enables_app_orchestration=True,
        enables_app_topology_planning=True,
        durable_runtime_dir="app_matrix",
    ),
    "admin": ModeProfile(
        name="admin",
        label=get_root_mode_label("admin"),
        attach_admin_runtime_by_default=True,
        enables_interactive_chat=True,
        enables_durable_goal_loop=True,
        enables_app_orchestration=True,
        enables_app_topology_planning=True,
        durable_runtime_dir="admin",
    ),
}


def resolve_mode_profile(root_mode: str | None) -> ModeProfile:
    normalized = normalize_root_mode(root_mode)
    return _MODE_PROFILES[normalized]


def list_mode_profiles() -> list[dict[str, Any]]:
    return [profile.to_dict() for profile in _MODE_PROFILES.values()]


def get_mode_capability_label(capability_name: str) -> str:
    return _CAPABILITY_LABELS.get(capability_name, capability_name)
