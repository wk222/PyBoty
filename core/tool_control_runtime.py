"""Policy and approval orchestration helpers for tool middleware."""

from __future__ import annotations

import json
from collections.abc import Sequence
from typing import Any

from langchain_core.messages import ToolMessage
from langgraph.types import interrupt

from .agent_control import AgentControlPolicy, ToolControlDecision, ToolRiskLevel
from .tool_middleware_observability import ToolMiddlewareObservability


class ToolControlRuntime:
    """Apply control policy decisions and approval interrupts for tool calls."""

    def __init__(
        self,
        *,
        control_policy: AgentControlPolicy,
        approval_scope: str,
        observability: ToolMiddlewareObservability | None = None,
    ):
        self.control_policy = control_policy
        self.approval_scope = approval_scope
        self._observability = observability or ToolMiddlewareObservability(
            max_recent_calls=control_policy.max_recent_tool_calls,
            stuck_loop_threshold=control_policy.stuck_loop_warning_threshold,
            stuck_loop_kill_threshold=control_policy.stuck_loop_kill_threshold,
        )

    def get_usage_stats(self) -> dict[str, int]:
        return self._observability.get_usage_stats()

    def reset_usage_stats(self) -> None:
        self._observability.reset_usage_stats()

    def get_stuck_loop_stats(self) -> dict[str, Any]:
        return self._observability.get_stuck_loop_stats()

    def build_snapshot(
        self,
        *,
        known_tools: list[str],
        approval_queue: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return self._observability.build_snapshot(
            policy=self.control_policy.to_dict(),
            known_tools=known_tools,
            approval_queue=approval_queue,
        )

    def increment_usage(self, tool_name: str) -> None:
        self._observability.increment_usage(tool_name)

    def check_stuck_loop(self, tool_name: str, tool_args: dict[str, Any]) -> str | None:
        return self._observability.check_stuck_loop(tool_name, tool_args)

    def enforce_tool_call(
        self,
        *,
        tool_name: str,
        tool_args: dict[str, Any],
        tool_call_id: str,
        is_dynamic: bool,
    ) -> ToolMessage | None:
        decision = self.control_policy.evaluate_tool_call(tool_name, is_dynamic=is_dynamic)
        self.record_control_event(
            tool_name=tool_name,
            tool_args=tool_args,
            decision=decision,
            tool_call_id=tool_call_id,
        )

        if not decision.allowed:
            return self.build_error_message(
                tool_call_id=tool_call_id,
                payload={
                    "success": False,
                    "error": f"CONTROL_POLICY_BLOCKED: {decision.reason}",
                    "tool_name": tool_name,
                    "risk_level": decision.risk_level.value,
                    "control_tags": list(decision.control_tags),
                },
            )

        if decision.risk_level in {ToolRiskLevel.HIGH, ToolRiskLevel.CRITICAL}:
            print(f"[DynamicToolMiddleware] 高风险工具调用: {tool_name} ({decision.risk_level.value})")

        return None

    def interrupt_for_pending_approvals(
        self,
        *,
        last_message: Any,
        tool_calls: Sequence[Any],
        dynamic_tool_names: set[str],
    ) -> dict[str, Any] | None:
        approval_indices: list[int] = []
        action_requests: list[dict[str, Any]] = []
        review_configs: list[dict[str, Any]] = []
        decisions_by_index: dict[int, ToolControlDecision] = {}

        for idx, tool_call in enumerate(tool_calls):
            tool_name = self._tool_name(tool_call)
            tool_args = self._tool_args(tool_call)
            decision = self.control_policy.evaluate_tool_call(tool_name, is_dynamic=tool_name in dynamic_tool_names)
            if not decision.requires_approval:
                continue
            approval_indices.append(idx)
            decisions_by_index[idx] = decision
            action_requests.append(
                {
                    "name": tool_name,
                    "args": tool_args,
                    "tool_call_id": self._tool_call_id(tool_call),
                    "risk_level": decision.risk_level.value,
                    "control_tags": list(decision.control_tags),
                }
            )
            review_configs.append(
                {
                    "action_name": tool_name,
                    "allowed_decisions": ["approve", "reject"],
                }
            )
            self.record_control_event(
                tool_name=tool_name,
                tool_args=tool_args,
                decision=ToolControlDecision(
                    allowed=False,
                    risk_level=decision.risk_level,
                    requires_approval=True,
                    reason="等待人工审批",
                    control_tags=tuple(list(decision.control_tags) + ["approval-pending"]),
                ),
                tool_call_id=self._tool_call_id(tool_call),
            )

        if not action_requests:
            return None

        resumed = interrupt(
            {
                "kind": "tool_call",
                "scope": self.approval_scope,
                "action_requests": action_requests,
                "review_configs": review_configs,
            }
        )
        decisions = self.extract_resume_decisions(resumed, expected_count=len(action_requests))

        revised_tool_calls: list[Any] = []
        artificial_messages: list[ToolMessage] = []
        decision_idx = 0

        for idx, tool_call in enumerate(tool_calls):
            if idx not in approval_indices:
                revised_tool_calls.append(tool_call)
                continue

            decision = decisions[decision_idx]
            decision_idx += 1
            control_decision = decisions_by_index[idx]
            processed_tool_call, tool_message = self.apply_approval_decision(
                tool_call=tool_call,
                decision=decision,
                control_decision=control_decision,
            )
            if processed_tool_call is not None:
                revised_tool_calls.append(processed_tool_call)
            if tool_message is not None:
                artificial_messages.append(tool_message)

        last_message.tool_calls = revised_tool_calls
        return {"messages": [last_message, *artificial_messages]}

    def apply_approval_decision(
        self,
        *,
        tool_call: Any,
        decision: dict[str, Any],
        control_decision: ToolControlDecision,
    ) -> tuple[Any | None, ToolMessage | None]:
        tool_name = self._tool_name(tool_call)
        tool_args = self._tool_args(tool_call)
        tool_call_id = self._tool_call_id(tool_call)
        decision_type = str(decision.get("type", "")).strip().lower()

        if decision_type == "approve":
            self.record_control_event(
                tool_name=tool_name,
                tool_args=tool_args,
                decision=ToolControlDecision(
                    allowed=True,
                    risk_level=control_decision.risk_level,
                    control_tags=tuple(list(control_decision.control_tags) + ["approval-approved"]),
                ),
                tool_call_id=tool_call_id,
            )
            return tool_call, None

        if decision_type == "reject":
            note = str(decision.get("message", "")).strip() or "人工审批未通过"
            self.record_control_event(
                tool_name=tool_name,
                tool_args=tool_args,
                decision=ToolControlDecision(
                    allowed=False,
                    risk_level=control_decision.risk_level,
                    requires_approval=True,
                    reason=note,
                    control_tags=tuple(list(control_decision.control_tags) + ["approval-rejected"]),
                ),
                tool_call_id=tool_call_id,
            )
            return None, ToolMessage(
                content=note,
                name=tool_name,
                tool_call_id=tool_call_id,
                status="error",
            )

        raise ValueError(f"Unsupported tool approval decision: {decision!r}")

    def log_tool_result(
        self,
        *,
        tool_name: str,
        tool_args: dict[str, Any],
        tool_call_id: str,
        result: ToolMessage,
        is_dynamic: bool,
    ) -> None:
        decision = ToolControlDecision(
            allowed=result.status != "error",
            risk_level=self.control_policy.evaluate_tool_call(tool_name, is_dynamic=is_dynamic).risk_level,
            reason=None if result.status != "error" else str(result.content)[:200],
            control_tags=("tool-result",),
        )
        self.record_control_event(
            tool_name=tool_name,
            tool_args=tool_args,
            decision=decision,
            tool_call_id=tool_call_id,
        )

    def record_control_event(
        self,
        *,
        tool_name: str,
        tool_args: dict[str, Any],
        decision: ToolControlDecision,
        tool_call_id: str | None = None,
    ) -> None:
        self._observability.record_control_event(
            tool_name=tool_name,
            tool_args=tool_args,
            decision=decision,
            tool_call_id=tool_call_id,
        )

    @staticmethod
    def extract_resume_decisions(resume_payload: Any, *, expected_count: int) -> list[dict[str, Any]]:
        if not isinstance(resume_payload, dict):
            raise ValueError("Tool approval resume payload must be a dict")
        decisions = resume_payload.get("decisions")
        if not isinstance(decisions, list):
            raise ValueError("Tool approval resume payload is missing decisions")
        if len(decisions) != expected_count:
            raise ValueError(f"Tool approval decision count mismatch: expected {expected_count}, got {len(decisions)}")
        normalized = []
        for item in decisions:
            if not isinstance(item, dict):
                raise ValueError(f"Invalid tool approval decision payload: {item!r}")
            normalized.append(item)
        return normalized

    @staticmethod
    def build_error_message(*, tool_call_id: str, payload: dict[str, Any]) -> ToolMessage:
        return ToolMessage(
            content=json.dumps(payload, ensure_ascii=False),
            tool_call_id=tool_call_id,
            status="error",
        )

    @staticmethod
    def _tool_name(tool_call: Any) -> str:
        if isinstance(tool_call, dict):
            return str(tool_call.get("name", ""))
        return str(getattr(tool_call, "name", ""))

    @staticmethod
    def _tool_args(tool_call: Any) -> dict[str, Any]:
        if isinstance(tool_call, dict):
            args = tool_call.get("args", {})
        else:
            args = getattr(tool_call, "args", {})
        return args if isinstance(args, dict) else {}

    @staticmethod
    def _tool_call_id(tool_call: Any) -> str:
        if isinstance(tool_call, dict):
            return str(tool_call.get("id", ""))
        return str(getattr(tool_call, "id", ""))
