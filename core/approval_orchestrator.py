"""Shared approval-resolution orchestration for web and service surfaces."""

from __future__ import annotations

import inspect
from collections.abc import Callable
from typing import Any

from .approval_queue import ApprovalQueue


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
        metadata = request.metadata if request is not None else {}

        parent_thread_id = str(metadata.get("parent_thread_id", "")).strip()
        target = str(metadata.get("target", "")).strip()

        if parent_thread_id:
            agent = self._get_agent_for_thread(parent_thread_id)
            result = self._resolve_agent_approval(
                agent,
                approval_id,
                approved=approved,
                note=note,
                approver=approver,
                resolution_labels=resolution_labels,
            )
        elif (
            request is not None
            and request.kind == "tool_call"
            and (target == "root_agent" or target.startswith("subagent:"))
        ):
            thread_id = str(metadata.get("thread_id", "")).strip()
            if target == "root_agent" and thread_id:
                agent = self._get_agent_for_thread(thread_id)
            else:
                agent = self._get_system_agent()
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

        workflow_id = str(metadata.get("workflow_id", "")).strip()
        workflow_resume_token = str(metadata.get("workflow_resume_token", "")).strip()
        workflow_pause_kind = str(metadata.get("workflow_pause_kind", "")).strip()
        if (
            result.get("success")
            and request is not None
            and request.kind == "tool_call"
            and workflow_pause_kind == "delegated_subagent"
            and workflow_id
            and workflow_resume_token
        ):
            workflow_result = self._get_system_agent().pyflow_engine.resume_workflow(
                workflow_id,
                workflow_resume_token,
                approved,
                approval_id=approval_id,
                note=note,
                resolved_by=approver,
            )
            result["subagent_result"] = result.get("result")
            result["result"] = workflow_result

        return result
