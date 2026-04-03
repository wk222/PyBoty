"""Workflow-specific orchestration for delegated subagent approvals."""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any

from core.systems.governance.delegated_approval_runtime import DelegatedApprovalResolutionRuntime
from core.systems.governance.approval_queue import ApprovalQueue

from .workflow_exceptions import WorkflowApprovalPause
from .workflow_models import FlowNode, NodeStatus, WorkflowDef, WorkflowStatus
from .workflow_pause_state import extract_waiting_approval_ids, primary_waiting_approval_id


class WorkflowDelegationRuntime:
    """Resume and requeue delegated workflow nodes that pause on subagent approvals."""

    def __init__(
        self,
        *,
        approval_queue: ApprovalQueue,
        save_workflow: Callable[[WorkflowDef], None],
    ):
        self.approval_queue = approval_queue
        self._save_workflow = save_workflow
        self._resolution_runtime = DelegatedApprovalResolutionRuntime(approval_queue=approval_queue)

    def resume_delegated_node(
        self,
        *,
        workflow: WorkflowDef,
        node: FlowNode,
        approval_id: str,
        note: str,
        resolved_by: str,
        run_workflow: Callable[[WorkflowDef], dict[str, Any]],
        resume_collaboration_node: (
            Callable[[FlowNode, WorkflowDef, dict[str, Any], dict[str, Any], str], dict[str, Any] | None] | None
        ) = None,
    ) -> dict[str, Any]:
        waiting_payload = node.output if isinstance(node.output, dict) else {}
        resolved_approval_id = str(approval_id).strip() or primary_waiting_approval_id(waiting_payload)
        if not resolved_approval_id:
            return {"success": False, "error": f"工作流节点 '{node.id}' 缺少委派审批 ID"}

        resolved_payload = self._resolution_runtime.resolve_payload(
            resolved_approval_id,
            agent_name=str(waiting_payload.get("agent_name", "")),
            task=str(waiting_payload.get("task", "")),
        )
        pause_mode = str(waiting_payload.get("workflow_pause_mode", "")).strip()

        if resolved_payload["status"] == "waiting_approval" and resolved_payload.get("approval_id"):
            return self.queue_waiting_delegate_result(
                workflow=workflow,
                node=node,
                waiting_result={
                    **waiting_payload,
                    **resolved_payload,
                    "workflow_pause_kind": "delegated_subagent",
                    "workflow_pause_mode": pause_mode,
                    "workflow_pause_state": waiting_payload.get("workflow_pause_state", {}),
                },
            )

        if pause_mode in {"debate", "consensus"} and resume_collaboration_node is not None:
            continued_result = resume_collaboration_node(
                node,
                workflow,
                waiting_payload,
                resolved_payload,
                resolved_approval_id,
            )
            if continued_result is None:
                return {"success": False, "error": f"工作流节点 '{node.id}' 无法恢复委派状态"}
            if continued_result.get("status") == "waiting_approval" and primary_waiting_approval_id(continued_result):
                return self.queue_waiting_delegate_result(
                    workflow=workflow,
                    node=node,
                    waiting_result=continued_result,
                )

            node.status = NodeStatus.COMPLETED
            node.output = continued_result
            node.completed_at = time.time()
            workflow.variables[f"{node.id}.output"] = node.output
            workflow.variables[f"{node.id}.status"] = "completed"
            workflow.variables.pop(f"{node.id}.approval_id", None)
            workflow.resume_token = None
            workflow.status = WorkflowStatus.RUNNING
            return run_workflow(workflow)

        node.status = NodeStatus.COMPLETED
        node.output = self.compose_delegated_node_output(
            node=node,
            waiting_payload=waiting_payload,
            resolved_payload=resolved_payload,
            note=note,
            resolved_by=resolved_by,
        )
        node.completed_at = time.time()
        workflow.variables[f"{node.id}.output"] = node.output
        workflow.variables[f"{node.id}.status"] = "completed"
        workflow.variables.pop(f"{node.id}.approval_id", None)
        workflow.resume_token = None
        workflow.status = WorkflowStatus.RUNNING
        return run_workflow(workflow)

    def queue_waiting_delegate_result(
        self,
        *,
        workflow: WorkflowDef,
        node: FlowNode,
        waiting_result: dict[str, Any],
    ) -> dict[str, Any]:
        approval_ids = extract_waiting_approval_ids(waiting_result)
        new_approval_id = primary_waiting_approval_id(waiting_result)
        if not new_approval_id:
            return {"success": False, "error": "新的委派审批缺少 approval_id"}

        if not workflow.resume_token:
            workflow.resume_token = f"resume-{workflow.id}-{int(time.time())}"

        prompt = str(waiting_result.get("response", "")).strip() or "子智能体继续执行后再次暂停。"
        for pending_approval_id in approval_ids or [new_approval_id]:
            request = self.approval_queue.get_request(pending_approval_id)
            if request is None:
                continue
            self.approval_queue.update_request_metadata(
                pending_approval_id,
                workflow_id=workflow.id,
                workflow_name=workflow.name,
                workflow_node_id=node.id,
                workflow_node_label=node.label,
                workflow_resume_token=workflow.resume_token,
                workflow_pause_kind="delegated_subagent",
                workflow_pause_mode=str(waiting_result.get("workflow_pause_mode", "")).strip(),
            )
            if pending_approval_id == new_approval_id:
                prompt = request.prompt or prompt

        node.status = NodeStatus.WAITING
        node.output = {
            **waiting_result,
            "resume_token": workflow.resume_token,
        }
        workflow.status = WorkflowStatus.PAUSED
        workflow.variables[f"{node.id}.status"] = "waiting_approval"
        workflow.variables[f"{node.id}.approval_id"] = new_approval_id
        workflow.variables[f"{node.id}.output"] = node.output
        self._save_workflow(workflow)
        response = self.approval_response(
            WorkflowApprovalPause(
                workflow_id=workflow.id,
                node_id=node.id,
                resume_token=workflow.resume_token or "",
                prompt=prompt,
                approval_id=new_approval_id,
            )
        )
        if len(approval_ids) > 1:
            response["approval_ids"] = approval_ids
        if isinstance(waiting_result.get("pending_approvals"), list):
            response["pending_approvals"] = waiting_result["pending_approvals"]
        return response

    @staticmethod
    def compose_delegated_node_output(
        *,
        node: FlowNode,
        waiting_payload: dict[str, Any],
        resolved_payload: dict[str, Any],
        note: str,
        resolved_by: str,
    ) -> dict[str, Any]:
        base_output = {
            "status": resolved_payload["status"],
            "success": resolved_payload["success"],
            "response": resolved_payload["response"],
            "approval_id": resolved_payload.get("approval_id"),
            "thread_id": resolved_payload.get("thread_id"),
            "state_update": resolved_payload.get("state_update", {}),
            "state_keys": resolved_payload.get("state_keys", []),
            "delegation": resolved_payload,
            "approval_note": note,
            "approval_resolved_by": resolved_by,
        }
        pause_mode = str(waiting_payload.get("workflow_pause_mode", "")).strip()
        if pause_mode == "supervisor":
            return {
                **base_output,
                "task": waiting_payload.get("task", ""),
                "chosen_worker": waiting_payload.get("chosen_worker", resolved_payload.get("agent_name", "")),
            }
        if pause_mode == "agent":
            return {
                **base_output,
                "agent_name": waiting_payload.get("agent_name", resolved_payload.get("agent_name", "")),
                "context": waiting_payload.get("context", ""),
            }
        return {"node_type": node.type.value, **base_output}

    @staticmethod
    def approval_response(approval_pause: WorkflowApprovalPause) -> dict[str, Any]:
        return {
            "status": "waiting_approval",
            "workflow_id": approval_pause.workflow_id,
            "node_id": approval_pause.node_id,
            "resume_token": approval_pause.resume_token,
            "prompt": approval_pause.prompt,
            "approval_id": approval_pause.approval_id,
        }
