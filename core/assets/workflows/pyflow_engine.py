"""PyFlow v2 workflow engine — thin orchestration shell over dedicated runtimes."""

from __future__ import annotations

import json
import logging
import os
from collections.abc import Callable
from typing import Any

from core.systems.governance import ApprovalQueue
from core.assets.workflows.workflow_engine_factory import build_workflow_engine_runtime_bundle, default_log_event
from core.assets.workflows.workflow_models import NodeStatus, WorkflowDef, WorkflowStatus

logger = logging.getLogger(__name__)


class PyFlowEngine:
    """DAG workflow engine that delegates all logic to dedicated runtime modules."""

    def __init__(
        self,
        workspace_dir: str = "workspace",
        approval_queue: ApprovalQueue | None = None,
        session_runtime: Any | None = None,
    ):
        self.workspace_dir = workspace_dir
        self.approval_queue = approval_queue or ApprovalQueue()
        bundle = build_workflow_engine_runtime_bundle(
            workspace_dir=workspace_dir,
            approval_queue=self.approval_queue,
            log_event=default_log_event,
            session_runtime=session_runtime,
        )
        bundle.bind_engine_callbacks(
            run_workflow=self.run_workflow,
            resume_workflow=self.resume_workflow,
        )
        self.runtime_bundle = bundle
        self.workflows_dir = bundle.workflows_dir
        self.definition_runtime = bundle.definition_runtime
        self.lifecycle_runtime = bundle.lifecycle_runtime
        self.graph_runtime = bundle.graph_runtime
        self.collaboration_runtime = bundle.collaboration_runtime
        self.execution_runtime = bundle.execution_runtime
        self.node_runtime = bundle.node_runtime

    def configure_callbacks(
        self,
        *,
        tool_callback: Callable | None = None,
        agent_callback: Callable | None = None,
        delegate_callback: Callable | None = None,
    ) -> None:
        """Wire tool/agent/delegate callbacks into the appropriate runtimes."""
        self.runtime_bundle.configure_callbacks(
            tool_callback=tool_callback,
            agent_callback=agent_callback,
            delegate_callback=delegate_callback,
        )

    def run_workflow(self, workflow: WorkflowDef) -> dict[str, Any]:
        return self.execution_runtime.run_workflow(workflow)

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
        return self.execution_runtime.resume_workflow(
            workflow_id,
            resume_token,
            approved,
            approval_id=approval_id,
            note=note,
            resolved_by=resolved_by,
        )

    def parse_workflow(self, definition: str) -> WorkflowDef:
        return self.definition_runtime.parse_workflow(definition)

    def save_workflow_file(self, workflow: WorkflowDef) -> str:
        return self.lifecycle_runtime.save_workflow_file(workflow)

    def load_workflow(self, name_or_file: str) -> WorkflowDef | None:
        return self.lifecycle_runtime.load_workflow(name_or_file)

    def list_workflow_files(self) -> list[dict[str, Any]]:
        return self.lifecycle_runtime.list_workflow_files()

    def list_active_workflows(self) -> list[dict[str, Any]]:
        return self.lifecycle_runtime.list_active_workflows()

    def get_pending_approvals(self) -> list[dict[str, Any]]:
        return self.lifecycle_runtime.get_pending_approvals()

    def get_workflow_graph(self, workflow_id: str) -> dict | None:
        return self.lifecycle_runtime.get_workflow_graph(workflow_id)

    def create_workflow_definition(self, name: str, definition: dict) -> str:
        return self.lifecycle_runtime.create_workflow_definition(name, definition)

    def update_workflow_definition(self, workflow_id: str, definition: dict) -> str:
        return self.lifecycle_runtime.update_workflow_definition(workflow_id, definition)

    def delete_workflow_definition(self, workflow_id: str) -> bool:
        return self.lifecycle_runtime.delete_workflow_definition(workflow_id)

    def get_workflow_definition(self, workflow_id: str) -> dict:
        return self.lifecycle_runtime.get_workflow_definition(workflow_id)

    # --- Version Control ---

    def publish_workflow(self, workflow_id: str, commit_id: str | None = None) -> dict[str, Any]:
        return self.lifecycle_runtime.publish_workflow(workflow_id, commit_id)

    def get_workflow_history(self, workflow_id: str, limit: int = 20) -> list[dict[str, Any]]:
        return self.lifecycle_runtime.get_workflow_history(workflow_id, limit)

    def get_workflow_version(self, workflow_id: str, commit_id: str) -> dict[str, Any] | None:
        return self.lifecycle_runtime.get_workflow_version(workflow_id, commit_id)

    def rollback_workflow(self, workflow_id: str, commit_id: str) -> dict[str, Any]:
        return self.lifecycle_runtime.rollback_workflow(workflow_id, commit_id)

    def get_workflow_meta(self, workflow_id: str) -> dict[str, Any]:
        return self.lifecycle_runtime.get_workflow_meta(workflow_id)

    # --- Startup Recovery ---

    def recover_paused_workflows(self) -> list[str]:
        """Scan ``.runs/`` for paused/waiting workflows and re-register them.

        Call this once during application startup so that approval-gated
        workflows survive server restarts.  Returns the list of recovered IDs.
        """
        runs_dir = os.path.join(self.workflows_dir, ".runs")
        if not os.path.isdir(runs_dir):
            return []

        recovered: list[str] = []
        recoverable = {WorkflowStatus.PAUSED.value, WorkflowStatus.RUNNING.value}

        for filename in os.listdir(runs_dir):
            if not filename.endswith(".json"):
                continue
            filepath = os.path.join(runs_dir, filename)
            try:
                with open(filepath, encoding="utf-8") as f:
                    data = json.load(f)
                status = data.get("status", "")
                if status not in recoverable:
                    continue
                wf = self.lifecycle_runtime.load_runtime(data.get("id", filename[:-5]))
                if wf is not None:
                    self.lifecycle_runtime.register_active_workflow(wf)
                    recovered.append(wf.id)
                    logger.info("Recovered workflow: %s (%s)", wf.name, wf.id)
            except Exception:
                logger.debug("Skip recovery for %s", filename, exc_info=True)

        if recovered:
            logger.info("Recovered %d paused/running workflow(s)", len(recovered))
        return recovered

    # --- Explicit Pause / Cancel ---

    def pause_workflow(self, workflow_id: str) -> dict[str, Any]:
        """Manually pause a running workflow."""
        wf = self.lifecycle_runtime.get_active_workflow(workflow_id)
        if wf is None:
            wf = self.lifecycle_runtime.load_runtime(workflow_id)
        if wf is None:
            return {"success": False, "error": f"工作流 '{workflow_id}' 不存在"}
        if wf.status != WorkflowStatus.RUNNING:
            return {"success": False, "error": f"工作流状态为 {wf.status.value}，无法暂停"}
        wf.status = WorkflowStatus.PAUSED
        self.lifecycle_runtime.save_runtime(wf)
        return {"success": True, "workflow_id": workflow_id, "status": "paused"}

    def send_signal(self, workflow_id: str, signal_name: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        """Send a signal to a paused workflow waiting on ``wait_signal``."""
        wf = self.lifecycle_runtime.get_active_workflow(workflow_id)
        if wf is None:
            wf = self.lifecycle_runtime.load_runtime(workflow_id)
        if wf is None:
            return {"success": False, "error": f"工作流 '{workflow_id}' 不存在"}
        if wf.status != WorkflowStatus.PAUSED:
            return {"success": False, "error": f"工作流状态为 {wf.status.value}，不在等待信号"}

        matched_node = None
        for node in wf.nodes.values():
            expected = wf.variables.get(f"{node.id}._signal_name")
            if expected == signal_name and node.status.value == "waiting":
                matched_node = node
                break

        if matched_node is None:
            return {"success": False, "error": f"没有找到等待信号 '{signal_name}' 的节点"}

        matched_node.output = {"signal": signal_name, "payload": payload or {}}
        matched_node.status = NodeStatus.COMPLETED
        matched_node.completed_at = __import__("time").time()
        wf.variables[f"{matched_node.id}.output"] = matched_node.output
        wf.variables[f"{matched_node.id}.status"] = "completed"

        resume_token = wf.variables.get(f"{matched_node.id}._resume_token", wf.resume_token or "")
        try:
            result = self.resume_workflow(wf.id, resume_token, True, note=f"signal:{signal_name}")
            return {"success": True, "result": result}
        except Exception as exc:
            return {"success": False, "error": str(exc)}

    def cancel_workflow(self, workflow_id: str) -> dict[str, Any]:
        """Cancel a running or paused workflow."""
        wf = self.lifecycle_runtime.get_active_workflow(workflow_id)
        if wf is None:
            wf = self.lifecycle_runtime.load_runtime(workflow_id)
        if wf is None:
            return {"success": False, "error": f"工作流 '{workflow_id}' 不存在"}
        if wf.status in (WorkflowStatus.COMPLETED, WorkflowStatus.CANCELLED):
            return {"success": False, "error": f"工作流已{wf.status.value}"}
        wf.status = WorkflowStatus.CANCELLED
        self.lifecycle_runtime.save_runtime(wf)
        return {"success": True, "workflow_id": workflow_id, "status": "cancelled"}
