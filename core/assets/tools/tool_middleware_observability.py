"""Observability and loop-detection helpers for tool middleware."""

from __future__ import annotations

import hashlib
import json
import time
from collections import Counter
from typing import Any

from core.systems.governance.agent_control import ToolControlDecision, ToolRiskLevel


class ToolMiddlewareObservability:
    def __init__(
        self,
        *,
        max_recent_calls: int,
        stuck_loop_threshold: int,
        stuck_loop_kill_threshold: int,
    ):
        self._tool_usage_stats: dict[str, int] = {}
        self._recent_calls: list[dict[str, Any]] = []
        self._control_events: list[dict[str, Any]] = []
        self._max_recent_calls = max_recent_calls
        self._stuck_loop_threshold = stuck_loop_threshold
        self._stuck_loop_kill_threshold = stuck_loop_kill_threshold

    def get_usage_stats(self) -> dict[str, int]:
        return self._tool_usage_stats.copy()

    def reset_usage_stats(self) -> None:
        self._tool_usage_stats.clear()

    def increment_usage(self, tool_name: str) -> None:
        self._tool_usage_stats[tool_name] = self._tool_usage_stats.get(tool_name, 0) + 1

    def get_stuck_loop_stats(self) -> dict[str, Any]:
        if not self._recent_calls:
            return {"recent_calls": 0, "unique_signatures": 0}
        sig_counts = Counter(call["signature"] for call in self._recent_calls)
        return {
            "recent_calls": len(self._recent_calls),
            "unique_signatures": len(sig_counts),
            "most_repeated": sig_counts.most_common(3),
        }

    def build_snapshot(
        self,
        *,
        policy: dict[str, Any],
        known_tools: list[str],
        approval_queue: dict[str, Any] | None = None,
        policy_pipeline: list[str] | None = None,
    ) -> dict[str, Any]:
        recent_events = self._control_events[-10:]
        blocked_count = sum(
            1 for event in self._control_events if (not event["allowed"]) or event.get("requires_approval", False)
        )
        high_risk_count = sum(
            1
            for event in self._control_events
            if event["risk_level"] in {ToolRiskLevel.HIGH.value, ToolRiskLevel.CRITICAL.value}
        )
        return {
            "policy": policy,
            "usage_stats": self.get_usage_stats(),
            "stuck_loop": self.get_stuck_loop_stats(),
            "approval_queue": approval_queue or {"pending": 0, "approved": 0, "rejected": 0, "recent": []},
            "observability": {
                "recent_events": recent_events,
                "blocked_count": blocked_count,
                "high_risk_events": high_risk_count,
                "known_tools": known_tools,
                "policy_pipeline": policy_pipeline or [],
            },
        }

    def record_control_event(
        self,
        *,
        tool_name: str,
        tool_args: dict[str, Any],
        decision: ToolControlDecision,
        tool_call_id: str | None = None,
    ) -> None:
        event = {
            "timestamp": time.time(),
            "tool_name": tool_name,
            "tool_call_id": tool_call_id,
            "allowed": decision.allowed,
            "requires_approval": decision.requires_approval,
            "risk_level": decision.risk_level.value,
            "reason": decision.reason,
            "control_tags": list(decision.control_tags),
            "args_preview": json.dumps(tool_args, ensure_ascii=False, default=str)[:200],
        }
        self._control_events.append(event)
        if len(self._control_events) > 50:
            self._control_events = self._control_events[-50:]

    def check_stuck_loop(self, tool_name: str, tool_args: dict[str, Any]) -> str | None:
        signature = self.compute_call_signature(tool_name, tool_args)
        self._recent_calls.append(
            {
                "tool_name": tool_name,
                "signature": signature,
                "timestamp": time.time(),
            }
        )
        if len(self._recent_calls) > self._max_recent_calls:
            self._recent_calls = self._recent_calls[-self._max_recent_calls :]

        identical_count = sum(1 for call in self._recent_calls if call["signature"] == signature)
        if identical_count >= self._stuck_loop_kill_threshold:
            print(f"[DynamicToolMiddleware] 卡死循环: {tool_name} 已重复 {identical_count} 次")
            self._recent_calls.clear()
            return json.dumps(
                {
                    "success": False,
                    "error": (
                        f"STUCK_LOOP_DETECTED: 工具 '{tool_name}' 已用相同参数调用 {identical_count} 次。"
                        "系统已强制中断。"
                    ),
                    "suggestion": "请停止重试相同动作，先分析失败原因，再换一种方法。",
                    "stuck_loop": True,
                },
                ensure_ascii=False,
            )
        if identical_count >= self._stuck_loop_threshold:
            print(f"[DynamicToolMiddleware] 卡死循环警告: {tool_name} 已重复 {identical_count} 次")
        return None

    @staticmethod
    def compute_call_signature(tool_name: str, tool_args: dict[str, Any]) -> str:
        args_str = json.dumps(tool_args, sort_keys=True, ensure_ascii=False, default=str)
        return hashlib.md5(f"{tool_name}:{args_str}".encode()).hexdigest()
