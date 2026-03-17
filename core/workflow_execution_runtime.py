"""Execution-loop runtime for workflow scheduling and approval resume."""

from __future__ import annotations

import time
import traceback
from collections.abc import Callable
from typing import Any

from .approval_queue import ApprovalQueue
from .workflow_delegation_runtime import WorkflowDelegationRuntime
from .workflow_models import (
    BRANCH_NODE_TYPES,
    FlowEdge,
    FlowNode,
    NodeExecutionRecord,
    NodeStatus,
    NodeType,
    WorkflowDef,
    WorkflowRunRecord,
    WorkflowStatus,
)
from .workflow_node_runtime import WorkflowApprovalPause


class WorkflowExecutionRuntime:
    """Run workflow DAGs and resume paused approval gates."""

    def __init__(
        self,
        *,
        approval_queue: ApprovalQueue,
        topo_sort: Callable[[WorkflowDef], list[str]],
        get_ready_nodes: Callable[[WorkflowDef], list[str]],
        get_successors: Callable[[WorkflowDef, str], list[FlowEdge]],
        exec_node: Callable[[FlowNode, WorkflowDef], Any],
        resolve_var: Callable[[str, WorkflowDef], Any],
        register_active_workflow: Callable[[WorkflowDef], None],
        save_workflow: Callable[[WorkflowDef], None],
        load_workflow: Callable[[str], WorkflowDef | None],
        get_active_workflow: Callable[[str], WorkflowDef | None],
        workflow_approval_fingerprint: Callable[..., str],
        log_event: Callable[[WorkflowDef, str, str, str], None],
        process_node_success: Callable[..., list[str]] | None = None,
        reset_edge_states: Callable[[WorkflowDef], None] | None = None,
        resume_collaboration_node: (
            Callable[[FlowNode, WorkflowDef, dict[str, Any], dict[str, Any], str], dict[str, Any] | None] | None
        ) = None,
    ):
        self.approval_queue = approval_queue
        self._topo_sort = topo_sort
        self._get_ready_nodes = get_ready_nodes
        self._get_successors = get_successors
        self._exec_node = exec_node
        self._resolve_var = resolve_var
        self._register_active_workflow = register_active_workflow
        self._save_workflow = save_workflow
        self._load_workflow = load_workflow
        self._get_active_workflow = get_active_workflow
        self._workflow_approval_fingerprint = workflow_approval_fingerprint
        self._log_event = log_event
        self._process_node_success = process_node_success
        self._reset_edge_states = reset_edge_states
        self._resume_collaboration_node = resume_collaboration_node
        self._delegation_runtime = WorkflowDelegationRuntime(
            approval_queue=approval_queue,
            save_workflow=save_workflow,
        )
        self._run_history: list[WorkflowRunRecord] = []
        self._current_run: WorkflowRunRecord | None = None

    @property
    def run_history(self) -> list[WorkflowRunRecord]:
        return list(self._run_history)

    def get_run(self, run_id: str) -> WorkflowRunRecord | None:
        for record in self._run_history:
            if record.run_id == run_id:
                return record
        return None

    def run_workflow(self, workflow: WorkflowDef) -> dict[str, Any]:
        import uuid as _uuid

        self._register_active_workflow(workflow)
        workflow.status = WorkflowStatus.RUNNING
        self._log_event(workflow, "_engine", "workflow_start", workflow.name)

        if self._reset_edge_states:
            self._reset_edge_states(workflow)

        workflow_inputs = workflow.variables.get("input", {})
        run_record = WorkflowRunRecord(
            run_id=_uuid.uuid4().hex[:12],
            workflow_id=workflow.id,
            workflow_name=workflow.name,
            inputs=dict(workflow_inputs) if isinstance(workflow_inputs, dict) else {},
            total_nodes=len(workflow.nodes),
        )
        self._current_run = run_record
        run_start = time.time()

        try:
            self._topo_sort(workflow)
        except ValueError as exc:
            workflow.status = WorkflowStatus.FAILED
            run_record.status = "error"
            run_record.error = str(exc)
            run_record.elapsed_time = time.time() - run_start
            self._run_history.append(run_record)
            return {"status": "error", "error": str(exc)}

        try:
            while True:
                ready = self._get_ready_nodes(workflow)
                if not ready:
                    break

                for node_id in ready:
                    node = workflow.nodes[node_id]
                    result = self._execute_ready_node(node, workflow)
                    if result is not None:
                        run_record.elapsed_time = time.time() - run_start
                        run_record.status = result.get("status", "unknown")
                        run_record.completed_nodes = sum(
                            1 for n in workflow.nodes.values() if n.status in (NodeStatus.COMPLETED, NodeStatus.SKIPPED)
                        )
                        self._run_history.append(run_record)
                        return result

            final = self._finalize_workflow(workflow)
            run_record.status = final.get("status", "completed")
            run_record.outputs = final.get("output")
            run_record.elapsed_time = time.time() - run_start
            run_record.completed_nodes = sum(
                1 for n in workflow.nodes.values() if n.status in (NodeStatus.COMPLETED, NodeStatus.SKIPPED)
            )
            self._run_history.append(run_record)
            return final
        except Exception as exc:
            workflow.status = WorkflowStatus.FAILED
            self._save_workflow(workflow)
            run_record.status = "error"
            run_record.error = str(exc)
            run_record.elapsed_time = time.time() - run_start
            self._run_history.append(run_record)
            return {"status": "error", "error": str(exc), "traceback": traceback.format_exc()}

    def resume_workflow(
        self,
        workflow_id: str,
        resume_token: str,
        approved: bool,
        *,
        approval_id: str = "",
        note: str = "",
        resolved_by: str = "",
    ) -> dict[str, Any]:
        workflow = self._get_active_workflow(workflow_id) or self._load_workflow(workflow_id)
        if not workflow:
            return {"success": False, "error": f"工作流 '{workflow_id}' 不存在"}
        if workflow.resume_token != resume_token:
            return {"success": False, "error": "无效的 resume token"}

        waiting_nodes = [node for node in workflow.nodes.values() if node.status == NodeStatus.WAITING]
        if not waiting_nodes:
            return {"success": False, "error": "没有等待审批的节点"}

        node = waiting_nodes[0]
        fingerprint = self._workflow_approval_fingerprint(
            workflow_id=workflow.id,
            node_id=node.id,
            resume_token=resume_token,
        )
        if node.type != NodeType.APPROVE:
            return self._delegation_runtime.resume_delegated_node(
                workflow=workflow,
                node=node,
                approval_id=approval_id,
                note=note,
                resolved_by=resolved_by,
                run_workflow=self.run_workflow,
                resume_collaboration_node=self._resume_collaboration_node,
            )

        if approved:
            node.status = NodeStatus.COMPLETED
            node.output = {"approved": True}
            node.completed_at = time.time()
            workflow.variables[f"{node.id}.output"] = node.output
            workflow.variables[f"{node.id}.status"] = "completed"
            workflow.resume_token = None
            workflow.status = WorkflowStatus.RUNNING
            result = self.run_workflow(workflow)
        else:
            node.status = NodeStatus.SKIPPED
            node.output = {"approved": False}
            workflow.status = WorkflowStatus.CANCELLED
            workflow.resume_token = None
            self._save_workflow(workflow)
            result = {"status": "cancelled", "workflow": workflow.to_dict()}

        self.approval_queue.finalize_request(
            kind="workflow_node",
            scope=f"workflow:{workflow_id}",
            fingerprint=fingerprint,
            approved=approved,
            note=note,
            resolved_by=resolved_by,
            result=result,
        )
        return result

    def _execute_ready_node(self, node: FlowNode, workflow: WorkflowDef) -> dict[str, Any] | None:
        if node.skip_condition and node.type not in (NodeType.START, NodeType.END):
            if not self._evaluate_skip_condition(node, workflow):
                node.status = NodeStatus.SKIPPED
                node.output = {"skipped": True, "reason": "condition not met"}
                node.completed_at = time.time()
                workflow.variables[f"{node.id}.output"] = node.output
                workflow.variables[f"{node.id}.status"] = "skipped"
                self._log_event(workflow, node.id, "node_skipped", f"condition: {node.skip_condition}")
                if self._process_node_success:
                    self._process_node_success(workflow, node.id)
                return None

        if node.type in BRANCH_NODE_TYPES:
            branch_key = "_route_target" if node.type == NodeType.ROUTER else "_branch"
            return self._execute_branch_node(node, workflow, branch_key=branch_key)
        return self._execute_standard_node(node, workflow)

    @staticmethod
    def _evaluate_skip_condition(node: FlowNode, workflow: WorkflowDef) -> bool:
        """Evaluate a node's skip_condition against workflow variables.

        Returns True if the condition passes (node should execute),
        False if it fails (node should be skipped).
        """
        expr = node.skip_condition
        if not expr:
            return True

        _SAFE_BUILTINS = {
            "len": len,
            "int": int,
            "float": float,
            "str": str,
            "bool": bool,
            "abs": abs,
            "min": min,
            "max": max,
            "sum": sum,
            "round": round,
            "sorted": sorted,
            "True": True,
            "False": False,
            "None": None,
        }

        for forbidden in ("import", "__", "exec", "eval", "compile", "open", "globals", "locals"):
            if forbidden in expr:
                return True  # safety: don't skip on suspicious expressions

        eval_ns: dict = dict(_SAFE_BUILTINS)
        for key, val in workflow.variables.items():
            safe_key = key.replace(".", "_").replace("-", "_")
            eval_ns[safe_key] = val

        try:
            result = eval(expr, {"__builtins__": {}}, eval_ns)  # noqa: S307
            return bool(result)
        except Exception:
            return True  # on error, don't skip — execute normally

    def _execute_branch_node(
        self,
        node: FlowNode,
        workflow: WorkflowDef,
        *,
        branch_key: str,
    ) -> dict[str, Any] | None:
        node_rec = self._start_node_record(node)
        try:
            result = self._exec_node(node, workflow)
            selected_target = result.get(branch_key) if isinstance(result, dict) else None
            if self._process_node_success:
                self._process_node_success(workflow, node.id, selected_target=selected_target)
            elif selected_target:
                self._skip_unselected_targets(workflow, node_id=node.id, selected_target=selected_target)
            self._finish_node_record(node_rec, node, result)
            return None
        except WorkflowApprovalPause as approval_pause:
            node_rec.status = "waiting_approval"
            self._record_node(node_rec, node)
            return self._approval_response(approval_pause)
        except Exception as exc:
            self._fail_node_record(node_rec, node, exc)
            return self._handle_node_failure(node=node, workflow=workflow, error=exc)

    def _execute_standard_node(self, node: FlowNode, workflow: WorkflowDef) -> dict[str, Any] | None:
        node_rec = self._start_node_record(node)
        try:
            result = self._exec_node(node, workflow)
            if self._process_node_success:
                self._process_node_success(workflow, node.id)
            self._finish_node_record(node_rec, node, result)
            return None
        except WorkflowApprovalPause as approval_pause:
            node_rec.status = "waiting_approval"
            self._record_node(node_rec, node)
            return self._approval_response(approval_pause)
        except Exception as exc:
            self._fail_node_record(node_rec, node, exc)
            return self._handle_node_failure(node=node, workflow=workflow, error=exc)

    def _start_node_record(self, node: FlowNode) -> NodeExecutionRecord:
        return NodeExecutionRecord(
            node_id=node.id,
            node_type=node.type.value,
            status="running",
            inputs={k: v for k, v in node.config.items() if k not in ("retries", "retry_delay", "continue_on_error")},
        )

    def _finish_node_record(self, rec: NodeExecutionRecord, node: FlowNode, result: Any) -> None:
        rec.status = "completed"
        rec.elapsed_time = (node.completed_at or time.time()) - (node.started_at or rec.created_at)
        if isinstance(result, dict):
            rec.outputs = {k: v for k, v in result.items() if not str(k).startswith("_")}
        else:
            rec.outputs = {"result": result}
        self._record_node(rec, node)

    def _fail_node_record(self, rec: NodeExecutionRecord, node: FlowNode, exc: Exception) -> None:
        rec.status = "failed"
        rec.error = str(exc)
        rec.elapsed_time = (node.completed_at or time.time()) - (node.started_at or rec.created_at)
        rec.retry_count = node.retry_count
        self._record_node(rec, node)

    def _record_node(self, rec: NodeExecutionRecord, node: FlowNode) -> None:
        if self._current_run:
            self._current_run.node_executions.append(rec)

    def _handle_node_failure(
        self,
        *,
        node: FlowNode,
        workflow: WorkflowDef,
        error: Exception,
    ) -> dict[str, Any] | None:
        if node.config.get("continue_on_error", False):
            return None
        workflow.status = WorkflowStatus.FAILED
        self._save_workflow(workflow)
        return {
            "status": "failed",
            "error": str(error),
            "failed_node": node.id,
            "workflow": workflow.to_dict(),
        }

    def _skip_unselected_targets(self, workflow: WorkflowDef, *, node_id: str, selected_target: str) -> None:
        for edge in self._get_successors(workflow, node_id):
            target_node = workflow.nodes.get(edge.target)
            if target_node is None or edge.target == selected_target:
                continue
            if not any(candidate.source != node_id for candidate in workflow.edges if candidate.target == edge.target):
                target_node.status = NodeStatus.SKIPPED

    def _finalize_workflow(self, workflow: WorkflowDef) -> dict[str, Any]:
        all_done = all(node.status in (NodeStatus.COMPLETED, NodeStatus.SKIPPED) for node in workflow.nodes.values())
        failed_nodes = [node_id for node_id, node in workflow.nodes.items() if node.status == NodeStatus.FAILED]
        pending_nodes = [node_id for node_id, node in workflow.nodes.items() if node.status == NodeStatus.PENDING]

        if failed_nodes:
            workflow.status = WorkflowStatus.FAILED
        elif all_done:
            workflow.status = WorkflowStatus.COMPLETED
        elif pending_nodes:
            workflow.status = WorkflowStatus.FAILED
            self._save_workflow(workflow)
            return {
                "status": "failed",
                "error": f"工作流未完成，{len(pending_nodes)} 个节点无法执行（可能缺少连接边）",
                "unreachable_nodes": pending_nodes,
                "workflow": workflow.to_dict(),
            }
        else:
            workflow.status = WorkflowStatus.COMPLETED

        self._save_workflow(workflow)
        self._log_event(workflow, "_engine", "workflow_end", workflow.status.value)

        return {
            "status": workflow.status.value,
            "workflow_id": workflow.id,
            "output": self.extract_output(workflow),
            "nodes_summary": {
                node_id: {
                    "status": node.status.value,
                    "output_preview": str(node.output)[:200] if node.output else None,
                }
                for node_id, node in workflow.nodes.items()
            },
        }

    def extract_output(self, workflow: WorkflowDef) -> Any:
        if workflow.output_mapping:
            return self._resolve_var(workflow.output_mapping, workflow)
        end_nodes = [node for node in workflow.nodes.values() if node.type == NodeType.END]
        if end_nodes:
            return end_nodes[0].output
        completed = [
            node
            for node in workflow.nodes.values()
            if node.status == NodeStatus.COMPLETED and node.type != NodeType.START
        ]
        if completed:
            return completed[-1].output
        return None

    @staticmethod
    def _approval_response(approval_pause: WorkflowApprovalPause) -> dict[str, Any]:
        return {
            "status": "waiting_approval",
            "workflow_id": approval_pause.workflow_id,
            "node_id": approval_pause.node_id,
            "resume_token": approval_pause.resume_token,
            "prompt": approval_pause.prompt,
            "approval_id": approval_pause.approval_id,
        }
