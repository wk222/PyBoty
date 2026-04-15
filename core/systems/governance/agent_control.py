"""Agent-system control policies inspired by tool-first boundary models."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any

TOOL_MUTATION_TOOLS = frozenset({"create_custom_tool", "remove_custom_tool"})
AGENT_MUTATION_TOOLS = frozenset({"create_agent", "remove_agent"})
AGENT_DELEGATION_TOOLS = frozenset({"delegate_to_agent"})
HOST_EXECUTION_TOOLS = frozenset({"exec_code", "iterative_test", "exec_shell", "execute_command", "run_command", "shell"})

HIGH_RISK_TOOLS = TOOL_MUTATION_TOOLS | AGENT_MUTATION_TOOLS | AGENT_DELEGATION_TOOLS | HOST_EXECUTION_TOOLS


class ToolRiskLevel(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


@dataclass(frozen=True)
class ToolControlDecision:
    allowed: bool
    risk_level: ToolRiskLevel
    requires_approval: bool = False
    reason: str | None = None
    control_tags: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "allowed": self.allowed,
            "risk_level": self.risk_level.value,
            "requires_approval": self.requires_approval,
            "reason": self.reason,
            "control_tags": list(self.control_tags),
        }


@dataclass(frozen=True)
class AgentControlPolicy:
    """Explicit control surface for the root agent runtime."""

    mode: str = "balanced"
    blocked_tools: frozenset[str] = field(default_factory=frozenset)
    blocked_dynamic_tools: frozenset[str] = field(default_factory=frozenset)
    risky_tools: frozenset[str] = field(default_factory=lambda: HIGH_RISK_TOOLS)
    approval_required_tools: frozenset[str] = field(default_factory=lambda: HIGH_RISK_TOOLS)
    approval_required_dynamic_tools: bool = False
    allow_dynamic_tools: bool = True
    allow_tool_mutation: bool = True
    allow_agent_mutation: bool = True
    allow_agent_delegation: bool = True
    max_subagent_depth: int = 3
    max_concurrent_subagents: int = 5
    subagent_timeout_seconds: int = 300
    max_recent_tool_calls: int = 20
    stuck_loop_warning_threshold: int = 3
    stuck_loop_kill_threshold: int = 6
    arg_regex_patterns: dict[str, dict[str, str]] = field(default_factory=dict)
    tool_budgets: dict[str, int] = field(default_factory=dict)

    @classmethod
    def from_config(cls, config: dict[str, Any] | None = None) -> AgentControlPolicy:
        raw = config or {}
        mode = str(raw.get("mode", "balanced")).strip().lower() or "balanced"

        preset = _preset_values(mode)
        blocked_tools = _merge_names(preset["blocked_tools"], raw.get("blocked_tools"))
        blocked_dynamic_tools = _merge_names(preset["blocked_dynamic_tools"], raw.get("blocked_dynamic_tools"))
        risky_tools = _merge_names(preset["risky_tools"], raw.get("risky_tools"))

        # approval_required_tools: explicit config overrides preset (allows empty list to disable)
        if "approval_required_tools" in raw:
            approval_required_tools = _merge_names(raw["approval_required_tools"])
        else:
            approval_required_tools = _merge_names(preset["approval_required_tools"])

        return cls(
            mode=mode,
            blocked_tools=blocked_tools,
            blocked_dynamic_tools=blocked_dynamic_tools,
            risky_tools=risky_tools,
            approval_required_tools=approval_required_tools,
            approval_required_dynamic_tools=bool(
                raw.get("approval_required_dynamic_tools", preset["approval_required_dynamic_tools"])
            ),
            allow_dynamic_tools=bool(raw.get("allow_dynamic_tools", preset["allow_dynamic_tools"])),
            allow_tool_mutation=bool(raw.get("allow_tool_mutation", preset["allow_tool_mutation"])),
            allow_agent_mutation=bool(raw.get("allow_agent_mutation", preset["allow_agent_mutation"])),
            allow_agent_delegation=bool(raw.get("allow_agent_delegation", preset["allow_agent_delegation"])),
            max_subagent_depth=int(raw.get("max_subagent_depth", preset["max_subagent_depth"])),
            max_concurrent_subagents=int(raw.get("max_concurrent_subagents", preset["max_concurrent_subagents"])),
            subagent_timeout_seconds=int(raw.get("subagent_timeout_seconds", preset["subagent_timeout_seconds"])),
            max_recent_tool_calls=int(raw.get("max_recent_tool_calls", preset["max_recent_tool_calls"])),
            stuck_loop_warning_threshold=int(
                raw.get("stuck_loop_warning_threshold", preset["stuck_loop_warning_threshold"])
            ),
            stuck_loop_kill_threshold=int(raw.get("stuck_loop_kill_threshold", preset["stuck_loop_kill_threshold"])),
            arg_regex_patterns=raw.get("arg_regex_patterns", preset.get("arg_regex_patterns", {})),
            tool_budgets=raw.get("tool_budgets", preset.get("tool_budgets", {})),
        )

    def merge_with_override(self, override: dict[str, Any] | str | None) -> AgentControlPolicy:
        """Merge this policy with an override, ensuring the result is never more permissive than self."""
        if not override:
            return self
            
        ov = {}
        if isinstance(override, str):
            import json
            try:
                parsed = json.loads(override)
                if isinstance(parsed, dict):
                    ov = parsed
            except Exception:
                pass
        elif isinstance(override, dict):
            ov = override
            
        if not ov:
            return self

        # 1. Boolean flags: can only be turned off by override, never turned on if base is off
        def _intersect_bool(base_val: bool, key: str) -> bool:
            if key in ov:
                return base_val and bool(ov[key])
            return base_val

        # 2. Lists (blocked/approval): override can add items, but cannot remove items that base has
        def _union_sets(base_set: frozenset[str], key: str) -> frozenset[str]:
            if key in ov:
                override_set = _merge_names(ov[key])
                return frozenset(base_set | set(override_set))
            return base_set

        # 3. Budgets: override can lower the budget, but cannot increase it beyond base
        merged_budgets = dict(self.tool_budgets)
        if "tool_budgets" in ov and isinstance(ov["tool_budgets"], dict):
            for t_name, t_budget in ov["tool_budgets"].items():
                if isinstance(t_budget, (int, float)):
                    if t_name in merged_budgets:
                        merged_budgets[t_name] = min(merged_budgets[t_name], int(t_budget))
                    else:
                        merged_budgets[t_name] = int(t_budget)

        # 4. Regex patterns: override can add new patterns or restrict existing ones
        merged_regex = {k: dict(v) for k, v in self.arg_regex_patterns.items()}
        if "arg_regex_patterns" in ov and isinstance(ov["arg_regex_patterns"], dict):
            for t_name, patterns in ov["arg_regex_patterns"].items():
                if not isinstance(patterns, dict):
                    continue
                if t_name not in merged_regex:
                    merged_regex[t_name] = {}
                for arg_name, pattern in patterns.items():
                    if isinstance(pattern, str):
                        merged_regex[t_name][arg_name] = pattern

        return AgentControlPolicy(
            mode=self.mode, # Mode inherited
            blocked_tools=_union_sets(self.blocked_tools, "blocked_tools"),
            blocked_dynamic_tools=_union_sets(self.blocked_dynamic_tools, "blocked_dynamic_tools"),
            risky_tools=_union_sets(self.risky_tools, "risky_tools"),
            approval_required_tools=_union_sets(self.approval_required_tools, "approval_required_tools"),
            approval_required_dynamic_tools=_union_sets(self.approval_required_dynamic_tools, "approval_required_dynamic_tools"),
            allow_dynamic_tools=_intersect_bool(self.allow_dynamic_tools, "allow_dynamic_tools"),
            allow_tool_mutation=_intersect_bool(self.allow_tool_mutation, "allow_tool_mutation"),
            allow_agent_mutation=_intersect_bool(self.allow_agent_mutation, "allow_agent_mutation"),
            allow_agent_delegation=_intersect_bool(self.allow_agent_delegation, "allow_agent_delegation"),
            max_subagent_depth=min(self.max_subagent_depth, int(ov.get("max_subagent_depth", self.max_subagent_depth))),
            max_concurrent_subagents=min(self.max_concurrent_subagents, int(ov.get("max_concurrent_subagents", self.max_concurrent_subagents))),
            subagent_timeout_seconds=min(self.subagent_timeout_seconds, float(ov.get("subagent_timeout_seconds", self.subagent_timeout_seconds))),
            max_recent_tool_calls=self.max_recent_tool_calls,
            stuck_loop_warning_threshold=self.stuck_loop_warning_threshold,
            stuck_loop_kill_threshold=self.stuck_loop_kill_threshold,
            arg_regex_patterns=merged_regex,
            tool_budgets=merged_budgets,
        )

    def evaluate_tool_call(self, tool_name: str, *, is_dynamic: bool = False) -> ToolControlDecision:
        name = (tool_name or "").strip()
        tags = list(self._classify_tags(name=name, is_dynamic=is_dynamic))
        risk_level = self._classify_risk(name=name, is_dynamic=is_dynamic)

        if not name:
            return ToolControlDecision(
                allowed=False,
                risk_level=ToolRiskLevel.HIGH,
                reason="工具调用缺少名称",
                control_tags=tuple(tags or ["invalid"]),
            )

        if name in self.blocked_tools:
            return ToolControlDecision(
                allowed=False,
                risk_level=ToolRiskLevel.CRITICAL,
                reason=f"工具 '{name}' 被控制策略显式禁用",
                control_tags=tuple(tags + ["blocked"]),
            )

        if is_dynamic and not self.allow_dynamic_tools:
            return ToolControlDecision(
                allowed=False,
                risk_level=ToolRiskLevel.HIGH,
                reason="当前策略禁止执行动态注册工具",
                control_tags=tuple(tags + ["dynamic-disabled"]),
            )

        if is_dynamic and name in self.blocked_dynamic_tools:
            return ToolControlDecision(
                allowed=False,
                risk_level=ToolRiskLevel.HIGH,
                reason=f"动态工具 '{name}' 被控制策略禁用",
                control_tags=tuple(tags + ["blocked"]),
            )

        if name in TOOL_MUTATION_TOOLS and not self.allow_tool_mutation:
            return ToolControlDecision(
                allowed=False,
                risk_level=ToolRiskLevel.CRITICAL,
                reason=f"当前策略禁止修改工具集: {name}",
                control_tags=tuple(tags + ["tool-mutation-disabled"]),
            )

        if name in AGENT_MUTATION_TOOLS and not self.allow_agent_mutation:
            return ToolControlDecision(
                allowed=False,
                risk_level=ToolRiskLevel.CRITICAL,
                reason=f"当前策略禁止修改智能体集: {name}",
                control_tags=tuple(tags + ["agent-mutation-disabled"]),
            )

        if name in AGENT_DELEGATION_TOOLS and not self.allow_agent_delegation:
            return ToolControlDecision(
                allowed=False,
                risk_level=ToolRiskLevel.CRITICAL,
                reason="当前策略禁止委派给子智能体",
                control_tags=tuple(tags + ["delegation-disabled"]),
            )

        requires_approval = name in self.approval_required_tools or (
            is_dynamic and self.approval_required_dynamic_tools
        )
        return ToolControlDecision(
            allowed=True,
            risk_level=risk_level,
            requires_approval=requires_approval,
            control_tags=tuple(tags),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "mode": self.mode,
            "allow_dynamic_tools": self.allow_dynamic_tools,
            "allow_tool_mutation": self.allow_tool_mutation,
            "allow_agent_mutation": self.allow_agent_mutation,
            "allow_agent_delegation": self.allow_agent_delegation,
            "max_subagent_depth": self.max_subagent_depth,
            "max_concurrent_subagents": self.max_concurrent_subagents,
            "subagent_timeout_seconds": self.subagent_timeout_seconds,
            "blocked_tools": sorted(self.blocked_tools),
            "blocked_dynamic_tools": sorted(self.blocked_dynamic_tools),
            "risky_tools": sorted(self.risky_tools),
            "approval_required_tools": sorted(self.approval_required_tools),
            "approval_required_dynamic_tools": self.approval_required_dynamic_tools,
            "max_recent_tool_calls": self.max_recent_tool_calls,
            "stuck_loop_warning_threshold": self.stuck_loop_warning_threshold,
            "stuck_loop_kill_threshold": self.stuck_loop_kill_threshold,
        }

    def _classify_risk(self, *, name: str, is_dynamic: bool) -> ToolRiskLevel:
        if name in AGENT_MUTATION_TOOLS or name in AGENT_DELEGATION_TOOLS:
            return ToolRiskLevel.CRITICAL
        if name in TOOL_MUTATION_TOOLS or name in self.risky_tools:
            return ToolRiskLevel.HIGH
        if is_dynamic:
            return ToolRiskLevel.MEDIUM
        return ToolRiskLevel.LOW

    def _classify_tags(self, *, name: str, is_dynamic: bool) -> tuple[str, ...]:
        tags: list[str] = []
        if is_dynamic:
            tags.append("dynamic")
        if name in TOOL_MUTATION_TOOLS:
            tags.append("tool-mutation")
        if name in AGENT_MUTATION_TOOLS:
            tags.append("agent-mutation")
        if name in AGENT_DELEGATION_TOOLS:
            tags.append("delegation")
        if name in self.risky_tools and "high-risk" not in tags:
            tags.append("high-risk")
        return tuple(tags)


def _preset_values(mode: str) -> dict[str, Any]:
    if mode == "open":
        return {
            "blocked_tools": frozenset(),
            "blocked_dynamic_tools": frozenset(),
            "risky_tools": HIGH_RISK_TOOLS,
            "approval_required_tools": frozenset(),
            "approval_required_dynamic_tools": False,
            "allow_dynamic_tools": True,
            "allow_tool_mutation": True,
            "allow_agent_mutation": True,
            "allow_agent_delegation": True,
            "max_subagent_depth": 4,
            "max_concurrent_subagents": 8,
            "subagent_timeout_seconds": 600,
            "max_recent_tool_calls": 30,
            "stuck_loop_warning_threshold": 4,
            "stuck_loop_kill_threshold": 8,
        }
    if mode == "strict":
        return {
            "blocked_tools": frozenset(),
            "blocked_dynamic_tools": frozenset(),
            "risky_tools": HIGH_RISK_TOOLS,
            "approval_required_tools": frozenset(),
            "approval_required_dynamic_tools": False,
            "allow_dynamic_tools": False,
            "allow_tool_mutation": False,
            "allow_agent_mutation": False,
            "allow_agent_delegation": False,
            "max_subagent_depth": 2,
            "max_concurrent_subagents": 2,
            "subagent_timeout_seconds": 180,
            "max_recent_tool_calls": 16,
            "stuck_loop_warning_threshold": 2,
            "stuck_loop_kill_threshold": 4,
        }
    return {
        "blocked_tools": frozenset(),
        "blocked_dynamic_tools": frozenset(),
        "risky_tools": HIGH_RISK_TOOLS,
        "approval_required_tools": HIGH_RISK_TOOLS,
        "approval_required_dynamic_tools": False,
        "allow_dynamic_tools": True,
        "allow_tool_mutation": True,
        "allow_agent_mutation": True,
        "allow_agent_delegation": True,
        "max_subagent_depth": 3,
        "max_concurrent_subagents": 5,
        "subagent_timeout_seconds": 300,
        "max_recent_tool_calls": 20,
        "stuck_loop_warning_threshold": 3,
        "stuck_loop_kill_threshold": 6,
    }


def _merge_names(*values: Any) -> frozenset[str]:
    merged: set[str] = set()
    for value in values:
        if not value:
            continue
        if isinstance(value, str):
            merged.add(value)
            continue
        for item in value:
            item_str = str(item).strip()
            if item_str:
                merged.add(item_str)
    return frozenset(sorted(merged))
