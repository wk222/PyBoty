"""Shared approval-resolution orchestration for web and service surfaces."""

from __future__ import annotations

import inspect
from collections.abc import Callable
from typing import Any

from .approval_queue import ApprovalQueue
from .execution_protocol import ApprovalResolutionContext


class ApprovalOrchestrator:
    """Route approval resolutions to the correct runtime and continue orchestration."""

    def __init__(
        self,
        *,
        approval_queue: ApprovalQueue,
        get_agent_for_thread: Callable[[str], Any],
        get_system_agent: Callable[[], Any],
    ):
        self.approval_queue = approval_queue
        self._get_agent_for_thread = get_agent_for_thread
        self._get_system_agent = get_system_agent

    @staticmethod
    def _resolve_agent_approval(
        agent: Any,
        approval_id: str,
        *,
        approved: bool,
        note: str,
        approver: str,
        resolution_labels: list[str] | tuple[str, ...] | None,
    ) -> dict[str, Any]:
        resolve_approval = agent.resolve_approval
        signature = inspect.signature(resolve_approval)
        supports_resolution_labels = "resolution_labels" in signature.parameters or any(
            parameter.kind == inspect.Parameter.VAR_KEYWORD for parameter in signature.parameters.values()
        )
        if supports_resolution_labels:
            return resolve_approval(
                approval_id,
                approved=approved,
                note=note,
                approver=approver,
                resolution_labels=resolution_labels,
            )
        return resolve_approval(
            approval_id,
            approved=approved,
            note=note,
            approver=approver,
        )

    def resolve(
        self,
        approval_id: str,
        *,
        approved: bool,
        note: str = "",
        approver: str = "",
        resolution_labels: list[str] | tuple[str, ...] | None = None,
    ) -> dict[str, Any]:
        request = self.approval_queue.get_request(approval_id)
        route = ApprovalResolutionContext.from_request(request)
        agent = route.resolve_agent(
            get_agent_for_thread=self._get_agent_for_thread,
            get_system_agent=self._get_system_agent,
        )

        if agent is not None:
            result = self._resolve_agent_approval(
                agent,
                approval_id,
                approved=approved,
                note=note,
                approver=approver,
                resolution_labels=resolution_labels,
            )
        else:
            result = self.approval_queue.resolve(
                approval_id,
                approved=approved,
                note=note,
                resolved_by=approver,
                resolution_labels=resolution_labels,
            )

        if (
            result.get("success")
            and route.request_kind == "tool_call"
            and route.workflow_pause.should_resume_delegated_subagent
        ):
            workflow_result = self._get_system_agent().pyflow_engine.resume_workflow(
                route.workflow_pause.workflow_id,
                route.workflow_pause.workflow_resume_token,
                approved,
                approval_id=approval_id,
                note=note,
                resolved_by=approver,
            )
            result["subagent_result"] = result.get("result")
            result["result"] = workflow_result

        return result
