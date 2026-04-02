"""Stage-based policy pipeline for tool-call governance."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from core.systems.governance.agent_control import AgentControlPolicy, ToolControlDecision, ToolRiskLevel


@dataclass(frozen=True)
class ToolPolicyContext:
    tool_name: str
    tool_args: dict[str, Any]
    tool_call_id: str
    is_dynamic: bool
    approval_scope: str
    control_policy: AgentControlPolicy
    recent_calls: int = 0


class ToolPolicyStage(Protocol):
    name: str

    def evaluate(self, context: ToolPolicyContext) -> ToolControlDecision | None: ...


@dataclass(frozen=True)
class PathPolicyStage:
    allowed_roots: tuple[str, ...] = ()
    path_arg_names: tuple[str, ...] = (
        "path",
        "file_path",
        "target_path",
        "directory",
        "dir",
        "workspace_dir",
    )
    name: str = "path_policy"

    def evaluate(self, context: ToolPolicyContext) -> ToolControlDecision | None:
        if not self.allowed_roots:
            return None

        allowed_roots = [Path(root).resolve() for root in self.allowed_roots if str(root).strip()]
        if not allowed_roots:
            return None

        for key, value in context.tool_args.items():
            if key not in self.path_arg_names:
                continue
            for item in _iter_path_values(value):
                path = Path(item)
                if ".." in path.parts:
                    return ToolControlDecision(
                        allowed=False,
                        risk_level=ToolRiskLevel.CRITICAL,
                        reason=f"路径参数 '{key}' 包含目录穿越: {item}",
                        control_tags=("path-policy", "path-traversal"),
                    )
                if not path.is_absolute():
                    continue
                resolved = path.resolve()
                if any(_is_relative_to(resolved, root) for root in allowed_roots):
                    continue
                return ToolControlDecision(
                    allowed=False,
                    risk_level=ToolRiskLevel.CRITICAL,
                    reason=f"路径参数 '{key}' 超出允许根目录: {item}",
                    control_tags=("path-policy", "path-outside-root"),
                )
        return None


@dataclass(frozen=True)
class RateLimitStage:
    max_calls_per_tool: int | None = None
    name: str = "rate_limit"

    def evaluate(self, context: ToolPolicyContext) -> ToolControlDecision | None:
        if self.max_calls_per_tool is None:
            return None
        if context.recent_calls < self.max_calls_per_tool:
            return None
        return ToolControlDecision(
            allowed=False,
            risk_level=ToolRiskLevel.HIGH,
            reason=f"工具 '{context.tool_name}' 已达到速率限制（{self.max_calls_per_tool} 次）",
            control_tags=("rate-limit",),
        )


@dataclass(frozen=True)
class RiskAssessmentStage:
    name: str = "risk_assessment"

    def evaluate(self, context: ToolPolicyContext) -> ToolControlDecision | None:
        return context.control_policy.evaluate_tool_call(
            context.tool_name,
            is_dynamic=context.is_dynamic,
        )


@dataclass
class ToolPolicyPipeline:
    stages: list[ToolPolicyStage] = field(default_factory=list)

    def evaluate(self, context: ToolPolicyContext) -> ToolControlDecision:
        approval_tags: list[str] = []
        risk_level = ToolRiskLevel.LOW
        approval_reason: str | None = None
        approval_required = False

        for stage in self.stages:
            decision = stage.evaluate(context)
            if decision is None:
                continue
            risk_level = _max_risk_level(risk_level, decision.risk_level)
            approval_tags.extend(decision.control_tags)
            if not decision.allowed:
                return ToolControlDecision(
                    allowed=False,
                    risk_level=decision.risk_level,
                    requires_approval=decision.requires_approval,
                    reason=decision.reason,
                    control_tags=tuple(_dedupe_tags(approval_tags)),
                )
            if decision.requires_approval:
                approval_required = True
                approval_reason = decision.reason or approval_reason

        return ToolControlDecision(
            allowed=True,
            risk_level=risk_level,
            requires_approval=approval_required or approval_reason is not None,
            reason=approval_reason,
            control_tags=tuple(_dedupe_tags(approval_tags)),
        )

    def describe(self) -> list[str]:
        return [getattr(stage, "name", type(stage).__name__) for stage in self.stages]


def build_default_tool_policy_pipeline(
    *,
    control_policy: AgentControlPolicy,
    allowed_roots: list[str] | tuple[str, ...] | None = None,
    max_calls_per_tool: int | None = None,
) -> ToolPolicyPipeline:
    return ToolPolicyPipeline(
        stages=[
            PathPolicyStage(allowed_roots=tuple(allowed_roots or ())),
            RateLimitStage(max_calls_per_tool=max_calls_per_tool),
            RiskAssessmentStage(),
        ]
    )


def _iter_path_values(value: Any) -> list[str]:
    if isinstance(value, str) and value.strip():
        return [value]
    if isinstance(value, (list, tuple)):
        return [str(item).strip() for item in value if str(item).strip()]
    return []


def _is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _max_risk_level(left: ToolRiskLevel, right: ToolRiskLevel) -> ToolRiskLevel:
    order = {
        ToolRiskLevel.LOW: 0,
        ToolRiskLevel.MEDIUM: 1,
        ToolRiskLevel.HIGH: 2,
        ToolRiskLevel.CRITICAL: 3,
    }
    return left if order[left] >= order[right] else right


def _dedupe_tags(tags: list[str]) -> list[str]:
    result: list[str] = []
    for tag in tags:
        if tag and tag not in result:
            result.append(tag)
    return result


__all__ = [
    "PathPolicyStage",
    "RateLimitStage",
    "RiskAssessmentStage",
    "ToolPolicyContext",
    "ToolPolicyPipeline",
    "ToolPolicyStage",
    "build_default_tool_policy_pipeline",
]
