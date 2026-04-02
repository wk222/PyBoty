"""Shared helpers for LangGraph tool-approval interrupts."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from langgraph.types import Command

from .approval_queue import ApprovalQueue, ApprovalRequest


@dataclass(frozen=True)
class ToolApprovalInterrupt:
    """Structured view over a pending tool-approval interrupt."""

    interrupt_id: str
    action_requests: list[dict[str, Any]]
    review_configs: list[dict[str, Any]]
    scope: str

    @property
    def action_count(self) -> int:
        return len(self.action_requests)

    def summary(self) -> str:
        tool_names = [str(item.get("name", "")).strip() for item in self.action_requests]
        tool_names = [name for name in tool_names if name]
        if not tool_names:
            return "工具调用审批"
        if len(tool_names) == 1:
            return f"工具调用审批: {tool_names[0]}"
        preview = ", ".join(tool_names[:3])
        if len(tool_names) > 3:
            preview += "..."
        return f"工具调用审批: {preview}"

    def prompt(self) -> str:
        lines = ["以下工具调用需要人工审批："]
        for action in self.action_requests:
            name = str(action.get("name", "")).strip() or "unknown_tool"
            args = action.get("args", {})
            lines.append(f"- {name}: {args}")
        return "\n".join(lines)

    def to_metadata(self, *, thread_id: str, target: str) -> dict[str, Any]:
        return {
            "thread_id": thread_id,
            "target": target,
            "interrupt_id": self.interrupt_id,
            "action_requests": self.action_requests,
            "review_configs": self.review_configs,
            "approval_scope": self.scope,
        }


@dataclass(frozen=True)
class DelegatedApprovalInterrupt:
    """A parent-agent interrupt caused by a delegated subagent approval."""

    interrupt_id: str
    approval_id: str
    tool_name: str
    tool_call_id: str
    scope: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "interrupt_id": self.interrupt_id,
            "approval_id": self.approval_id,
            "tool_name": self.tool_name,
            "tool_call_id": self.tool_call_id,
            "scope": self.scope,
        }


def extract_tool_approval_interrupts(response: dict[str, Any], *, scope: str) -> list[ToolApprovalInterrupt]:
    """Parse tool-approval interrupts from a LangGraph response payload."""
    interrupts = response.get("__interrupt__") or []
    parsed: list[ToolApprovalInterrupt] = []
    for item in interrupts:
        value = getattr(item, "value", None)
        interrupt_id = getattr(item, "id", "")
        if not isinstance(value, dict):
            continue
        action_requests = value.get("action_requests")
        review_configs = value.get("review_configs")
        if not isinstance(action_requests, list) or not isinstance(review_configs, list):
            continue
        parsed.append(
            ToolApprovalInterrupt(
                interrupt_id=str(interrupt_id),
                action_requests=[request for request in action_requests if isinstance(request, dict)],
                review_configs=[config for config in review_configs if isinstance(config, dict)],
                scope=scope,
            )
        )
    return parsed


def extract_delegated_approval_interrupts(
    response: dict[str, Any],
    *,
    scope: str,
) -> list[DelegatedApprovalInterrupt]:
    """Parse delegation-sourced approval interrupts from a LangGraph response payload."""
    interrupts = response.get("__interrupt__") or []
    parsed: list[DelegatedApprovalInterrupt] = []
    for item in interrupts:
        value = getattr(item, "value", None)
        interrupt_id = getattr(item, "id", "")
        if not isinstance(value, dict) or value.get("kind") != "delegated_tool_call":
            continue
        approval_id = str(value.get("approval_id", "")).strip()
        tool_name = str(value.get("tool_name", "")).strip()
        tool_call_id = str(value.get("tool_call_id", "")).strip()
        if not approval_id:
            continue
        parsed.append(
            DelegatedApprovalInterrupt(
                interrupt_id=str(interrupt_id),
                approval_id=approval_id,
                tool_name=tool_name,
                tool_call_id=tool_call_id,
                scope=scope,
            )
        )
    return parsed


def build_tool_approval_resume_command(
    approval: ToolApprovalInterrupt,
    *,
    approved: bool,
    note: str = "",
) -> Command:
    """Construct the LangGraph resume command for a paused tool-approval interrupt."""
    decision_type = "approve" if approved else "reject"
    decisions = []
    for action in approval.action_requests:
        decision: dict[str, Any] = {"type": decision_type}
        if "host_execution_plan" in action:
            decision["host_execution_plan"] = action["host_execution_plan"]
        if not approved and note.strip():
            decision["message"] = note.strip()
        decisions.append(decision)
    return Command(resume={"decisions": decisions})


def build_delegated_approval_resume_command(approval_id: str) -> Command:
    """Construct the LangGraph resume command for a delegated approval interrupt."""
    return Command(resume={"approval_id": approval_id})


def create_tool_approval_request(
    *,
    approval_queue: ApprovalQueue,
    approval: ToolApprovalInterrupt,
    thread_id: str,
    target: str,
    callback,
) -> ApprovalRequest:
    """Register a pending tool-approval interrupt in the shared approval queue."""
    action_labels = {"tool-call", "human-in-the-loop"}
    policy_tags: set[str] = set()
    for action in approval.action_requests:
        tool_name = str(action.get("name", "")).strip()
        if tool_name:
            action_labels.add(f"tool:{tool_name}")
        risk_level = str(action.get("risk_level", "")).strip()
        if risk_level:
            policy_tags.add(f"risk:{risk_level}")
        for tag in action.get("control_tags", []):
            tag_value = str(tag).strip()
            if tag_value:
                policy_tags.add(tag_value)
    return approval_queue.create_request(
        kind="tool_call",
        scope=approval.scope,
        summary=approval.summary(),
        prompt=approval.prompt(),
        metadata=approval.to_metadata(thread_id=thread_id, target=target),
        fingerprint=f"{approval.scope}:{thread_id}:{approval.interrupt_id}",
        callback=callback,
        labels=sorted(action_labels),
        policy_tags=sorted(policy_tags),
    )


def approval_interrupt_from_metadata(
    metadata: dict[str, Any] | None,
    *,
    fallback_scope: str = "",
) -> ToolApprovalInterrupt | None:
    """Rebuild a stored tool approval interrupt from persisted metadata."""
    if not isinstance(metadata, dict):
        return None

    interrupt_id = str(metadata.get("interrupt_id", "")).strip()
    action_requests = metadata.get("action_requests", [])
    review_configs = metadata.get("review_configs", [])
    scope = str(metadata.get("approval_scope", "")).strip() or fallback_scope

    if not interrupt_id or not isinstance(action_requests, list) or not isinstance(review_configs, list) or not scope:
        return None

    normalized_actions = [item for item in action_requests if isinstance(item, dict)]
    normalized_configs = [item for item in review_configs if isinstance(item, dict)]
    if not normalized_actions or not normalized_configs:
        return None

    return ToolApprovalInterrupt(
        interrupt_id=interrupt_id,
        action_requests=normalized_actions,
        review_configs=normalized_configs,
        scope=scope,
    )
