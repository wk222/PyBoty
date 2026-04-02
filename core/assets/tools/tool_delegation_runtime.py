"""Delegated subagent approval helpers for tool middleware."""

from __future__ import annotations

import json
from typing import Any

from langchain_core.messages import ToolMessage
from langgraph.types import interrupt

from core.delegated_approval_runtime import DelegatedApprovalResolutionRuntime
from core.systems.governance.approval_queue import ApprovalQueue


class DelegatedToolApprovalRuntime:
    """Pause and resume delegated tool calls that await subagent approval."""

    def __init__(self, *, approval_queue: ApprovalQueue, approval_scope: str):
        self.approval_queue = approval_queue
        self.approval_scope = approval_scope
        self._resolution_runtime = DelegatedApprovalResolutionRuntime(approval_queue=approval_queue)

    def handle_tool_result(
        self,
        *,
        tool_name: str,
        tool_call_id: str,
        result: ToolMessage,
    ) -> ToolMessage | None:
        if tool_name != "delegate_to_agent":
            return None

        payload = self._resolution_runtime.parse_waiting_payload(result.content)
        if not isinstance(payload, dict) or payload.get("status") != "waiting_approval":
            return None

        approval_id = str(payload.get("approval_id", "")).strip()
        if not approval_id:
            return None

        while True:
            resumed = interrupt(
                {
                    "kind": "delegated_tool_call",
                    "scope": self.approval_scope,
                    "approval_id": approval_id,
                    "tool_name": tool_name,
                    "tool_call_id": tool_call_id,
                }
            )
            approval_id = self._extract_resume_approval_id(resumed, approval_id=approval_id)
            resolved_payload = self._resolution_runtime.resolve_payload(
                approval_id,
                agent_name=str(payload.get("agent_name", "")),
                task=str(payload.get("task", "")),
            )
            if resolved_payload.get("status") == "waiting_approval" and resolved_payload.get("approval_id"):
                approval_id = str(resolved_payload["approval_id"]).strip()
                continue

            status = "success" if resolved_payload.get("success", True) else "error"
            return ToolMessage(
                content=json.dumps(resolved_payload, ensure_ascii=False, indent=2),
                tool_call_id=tool_call_id,
                status=status,
            )

    @staticmethod
    def _extract_resume_approval_id(resume_payload: Any, *, approval_id: str) -> str:
        if not isinstance(resume_payload, dict):
            return approval_id
        resumed_approval_id = str(resume_payload.get("approval_id", "")).strip()
        return resumed_approval_id or approval_id
