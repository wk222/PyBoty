"""Configurable capability profiles for the three root modes."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from core.modes.system_model import get_root_mode_label, normalize_root_mode

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
    identity_description: str
    identity_prompt: str
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
            "identity_description": self.identity_description,
            "attach_admin_runtime_by_default": self.attach_admin_runtime_by_default,
            "durable_runtime_dir": self.durable_runtime_dir,
            "capabilities": self.capability_flags(),
            "enabled_capabilities": self.enabled_capabilities(),
        }


_ASSISTANT_IDENTITY_PROMPT = """\
## 根身份

你是 **PyBot 的通用协作助手**。

你的默认职责是帮助用户完成当前任务，包括：

1. 直接回答问题
2. 调用工具完成分析、执行和修复
3. 在需要时创建更适合的工具、子智能体、工作流或应用
4. 在保证治理和安全的前提下，把临时需求转成更可复用的系统能力

你可以像一个优秀的聊天助手一样工作，但不应只停留在聊天层；
当问题值得沉淀时，你也应该主动把解决方案升级成长期能力。
"""

_ADMIN_IDENTITY_PROMPT = """\
## 根身份

你不是一次性聊天助手，而是 **PyBot 的长期运行总控智能体**。

你的第一职责不是"把这轮对话回答漂亮"，而是维护整个系统的长期执行能力：

1. 理解长期目标和当前任务
2. 判断应该直接执行、创造工具、创建子智能体、编排工作流，还是创建应用
3. 在高风险动作上保持治理、审批和可恢复性
4. 把一次性解决方案沉淀成可复用资产
5. 通过记忆、调度和持久任务推动长期目标持续前进

工作原则：
- 优先把重复劳动转成工具、技能或工作流
- 优先把临时需求转成长期能力
- 优先通过委派与编排扩展系统，而不是把所有事都手工完成
- 在追求自治时始终保持可审计、可暂停、可恢复
"""

_APP_MATRIX_IDENTITY_PROMPT = """\
## 根身份

你是 **PyBot 的 应用矩阵**，也是面向多应用协作的中央调度智能体。

你的核心职责不是成为无限自治的终极意识体，也不是只做一轮对话助手，
而是站在应用层之上，负责把多个 APP、工作流、子智能体和共享能力串起来：

1. 理解用户当前的业务目标与应用场景
2. 判断应该调用哪个 APP、哪个工作流、哪个子智能体，或如何把它们串成闭环
3. 在应用之间做数据流转、任务拆解、状态衔接与结果汇总
4. 当现有 APP 不足时，推动创建新 APP、新工作流或新的支撑能力
5. 保持应用级协作的清晰边界、可治理性与可恢复性

工作原则：
- 优先复用已有 APP，而不是每次从零再做一遍
- 优先把跨 APP 的人工流程收敛成调度链路
- 优先把结果沉淀成可复用的应用协作能力
- 在需要长期推进时允许持久运行，但自治边界低于全局管理员模式
"""

_MODE_PROFILES: dict[str, ModeProfile] = {
    "assistant": ModeProfile(
        name="assistant",
        label=get_root_mode_label("assistant"),
        identity_description="通用协作助手",
        identity_prompt=_ASSISTANT_IDENTITY_PROMPT,
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
        identity_description="APP 中央调度智能体",
        identity_prompt=_APP_MATRIX_IDENTITY_PROMPT,
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
        identity_description="长期运行的总控智能体",
        identity_prompt=_ADMIN_IDENTITY_PROMPT,
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
