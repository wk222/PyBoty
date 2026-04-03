"""Shared resolution helpers for delegated subagent approvals."""

from __future__ import annotations

import json
from typing import Any

from core.assets.agents.delegation_payload import normalize_delegation_payload

from .approval_queue import ApprovalQueue


class DelegatedApprovalResolutionRuntime:
    """Resolve delegated approval records into normalized orchestration payloads."""

    def __init__(self, *, approval_queue: ApprovalQueue):
        self.approval_queue = approval_queue

    def resolve_payload(
        self,
        approval_id: str,
        *,
        agent_name: str = "",
        task: str = "",
    ) -> dict[str, Any]:
        request = self.approval_queue.get_request(approval_id)
        if request is None:
            return normalize_delegation_payload(
                {
                    "success": False,
                    "status": "error",
                    "error": f"委派审批 '{approval_id}' 不存在",
                    "approval_id": approval_id,
                },
                agent_name=agent_name,
                task=task,
            )

        payload = request.resolution_result
        if isinstance(payload, str):
            return normalize_delegation_payload(payload, agent_name=agent_name, task=task)
        if isinstance(payload, dict) and str(payload.get("status", "")).strip().lower() != "recorded":
            return normalize_delegation_payload(payload, agent_name=agent_name, task=task)

        if request.status == "rejected":
            return normalize_delegation_payload(
                {
                    "success": False,
                    "status": "rejected",
                    "response": request.resolution_note or "人工审批未通过",
                    "approval_id": approval_id,
                },
                agent_name=agent_name,
                task=task,
            )

        if request.status == "pending":
            return normalize_delegation_payload(
                {
                    "success": False,
                    "status": "waiting_approval",
                    "approval_id": approval_id,
                },
                agent_name=agent_name,
                task=task,
            )

        return normalize_delegation_payload(
            {
                "success": request.approved is True,
                "status": "completed" if request.approved else "rejected",
                "approval_id": approval_id,
                "response": request.resolution_note or "",
            },
            agent_name=agent_name,
            task=task,
        )

    @staticmethod
    def parse_waiting_payload(content: Any) -> dict[str, Any] | None:
        if not isinstance(content, str):
            return None
        try:
            payload = json.loads(content)
        except Exception:
            return None
        return payload if isinstance(payload, dict) else None
