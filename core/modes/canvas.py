"""ExecutionCanvas — 会话级执行画布定义。

属于 modes/ 层（身份层），因为它决定 agent 的能力边界和资源使用策略。

与 ModeProfile（assistant/app_matrix/admin）正交：
- ModeProfile 决定 agent 的「职责角色」（做什么）
- ExecutionCanvas 决定 agent 的「资源策略」（用多少资源、多深的工具链）

三档画布直接映射到 AgentControlPolicy 的既有预设值：
- focused  → strict-like  省Token
- balanced → balanced      默认
- deep     → open-like     全能力 + 记忆蒸馏

设计约束（对齐 system_model.py）：
- 此文件只依赖 core/systems/ 层，不依赖 web/ 层
- web/state.py 从这里导入 presets，不在 web 层重新定义
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

CANVAS_NAMES = ("focused", "balanced", "deep")
DEFAULT_CANVAS = "balanced"


@dataclass(frozen=True)
class CanvasProfile:
    """单个画布的完整描述，包括 LLM 参数和 agent_control 覆盖。"""

    name: str
    label: str
    description: str
    color: str

    # LLM 参数覆盖（叠加在 llm_config 之上，不覆盖用户显式设置的值）
    llm_temperature: float
    llm_max_tokens: int

    # AgentControlPolicy 字段覆盖（与 agent_control.py _preset_values 对应）
    allow_dynamic_tools: bool
    allow_tool_mutation: bool
    allow_agent_mutation: bool
    allow_agent_delegation: bool
    max_subagent_depth: int
    max_concurrent_subagents: int
    max_recent_tool_calls: int
    stuck_loop_warning_threshold: int
    stuck_loop_kill_threshold: int

    # 记忆策略
    digest_on_every_turn: bool   # deep 模式每轮触发蒸馏
    digest_interval: int         # 非 deep 模式每 N 条消息触发蒸馏

    def to_control_overrides(self) -> dict[str, Any]:
        """返回可直接 merge 进 agent_control config 的字典。"""
        return {
            "allow_dynamic_tools": self.allow_dynamic_tools,
            "allow_tool_mutation": self.allow_tool_mutation,
            "allow_agent_mutation": self.allow_agent_mutation,
            "allow_agent_delegation": self.allow_agent_delegation,
            "max_subagent_depth": self.max_subagent_depth,
            "max_concurrent_subagents": self.max_concurrent_subagents,
            "max_recent_tool_calls": self.max_recent_tool_calls,
            "stuck_loop_warning_threshold": self.stuck_loop_warning_threshold,
            "stuck_loop_kill_threshold": self.stuck_loop_kill_threshold,
        }

    def to_llm_overrides(self) -> dict[str, Any]:
        """返回 LLM 参数覆盖字典（用 setdefault 叠加，不强制覆盖）。"""
        return {
            "temperature": self.llm_temperature,
            "max_tokens": self.llm_max_tokens,
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "label": self.label,
            "description": self.description,
            "color": self.color,
            "llm": self.to_llm_overrides(),
            "agent_control": self.to_control_overrides(),
            "digest_on_every_turn": self.digest_on_every_turn,
            "digest_interval": self.digest_interval,
        }


_CANVAS_REGISTRY: dict[str, CanvasProfile] = {
    "focused": CanvasProfile(
        name="focused",
        label="📊 精简",
        description="省Token：限制工具调用深度和 agent 委派，自动压缩上下文，低温短输出",
        color="#10b981",
        # LLM
        llm_temperature=0.3,
        llm_max_tokens=1500,
        # agent_control — 类似 strict 但保留动态工具
        allow_dynamic_tools=True,
        allow_tool_mutation=False,
        allow_agent_mutation=False,
        allow_agent_delegation=False,
        max_subagent_depth=1,
        max_concurrent_subagents=2,
        max_recent_tool_calls=10,
        stuck_loop_warning_threshold=2,
        stuck_loop_kill_threshold=4,
        # memory
        digest_on_every_turn=False,
        digest_interval=30,
    ),
    "balanced": CanvasProfile(
        name="balanced",
        label="⚡ 均衡",
        description="默认模式：标准工具链和 agent 协作，平衡效果与消耗",
        color="#818cf8",
        # LLM
        llm_temperature=0.7,
        llm_max_tokens=8192,
        # agent_control — balanced 预设
        allow_dynamic_tools=True,
        allow_tool_mutation=True,
        allow_agent_mutation=True,
        allow_agent_delegation=True,
        max_subagent_depth=3,
        max_concurrent_subagents=5,
        max_recent_tool_calls=20,
        stuck_loop_warning_threshold=3,
        stuck_loop_kill_threshold=6,
        # memory — 语义检索 + Garden + 中等频率蒸馏
        digest_on_every_turn=False,
        digest_interval=15,
    ),
    "deep": CanvasProfile(
        name="deep",
        label="🔮 深度",
        description="全能力：最大上下文窗口，完整 agent 委派，深度推理，对话结束后触发记忆蒸馏",
        color="#a855f7",
        # LLM
        llm_temperature=0.7,
        llm_max_tokens=16384,
        # agent_control — open 预设
        allow_dynamic_tools=True,
        allow_tool_mutation=True,
        allow_agent_mutation=True,
        allow_agent_delegation=True,
        max_subagent_depth=4,
        max_concurrent_subagents=8,
        max_recent_tool_calls=30,
        stuck_loop_warning_threshold=4,
        stuck_loop_kill_threshold=8,
        # memory — 全层检索 + 每轮蒸馏
        digest_on_every_turn=True,
        digest_interval=1,
    ),
}


def get_canvas_profile(name: str | None) -> CanvasProfile:
    """Return canvas profile, falling back to balanced if unknown."""
    return _CANVAS_REGISTRY.get(str(name or DEFAULT_CANVAS).strip().lower(), _CANVAS_REGISTRY[DEFAULT_CANVAS])


def get_canvas_with_overrides(
    name: str | None,
    overrides: dict[str, Any] | None = None,
) -> CanvasProfile:
    """Return a canvas profile with user-specified field overrides.

    Supported override keys: llm_max_tokens, llm_temperature,
    digest_interval, max_recent_tool_calls, max_subagent_depth, etc.
    """
    base = get_canvas_profile(name)
    if not overrides:
        return base

    fields = {f.name: getattr(base, f.name) for f in base.__dataclass_fields__.values()}
    for key, value in overrides.items():
        if key in fields:
            fields[key] = type(fields[key])(value)
    return CanvasProfile(**fields)


def list_canvas_profiles() -> list[dict[str, Any]]:
    return [p.to_dict() for p in _CANVAS_REGISTRY.values()]


def auto_select_canvas(prompt: str, message_count: int = 0) -> str:
    """Automatically select canvas based on prompt complexity.

    Returns the canvas name best suited for the given prompt.
    """
    text = prompt.lower()
    length = len(text)

    heavy_signals = [
        "analyze", "implement", "refactor", "design", "architect",
        "debug", "write a", "create a system", "build",
        "分析", "实现", "重构", "设计", "架构", "调试",
    ]
    light_signals = [
        "hello", "hi", "thanks", "ok", "yes", "no",
        "what is", "who is", "when", "how many",
        "你好", "谢谢", "好的", "是", "不",
    ]

    if any(s in text for s in heavy_signals) or length > 500:
        return "deep"

    if any(s in text for s in light_signals) and length < 100:
        return "focused"

    if message_count > 20:
        return "deep"

    return "balanced"
