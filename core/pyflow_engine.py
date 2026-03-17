"""PyFlow v2 workflow engine — thin orchestration shell over dedicated runtimes."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from core.approval_queue import ApprovalQueue
from core.workflow_engine_factory import build_workflow_engine_runtime_bundle, default_log_event
from core.workflow_models import WorkflowDef


class PyFlowEngine:
    """DAG workflow engine that delegates all logic to dedicated runtime modules."""

    def __init__(self, workspace_dir: str = "workspace", approval_queue: ApprovalQueue | None = None):
        self.workspace_dir = workspace_dir
        self.approval_queue = approval_queue or ApprovalQueue()
        bundle = build_workflow_engine_runtime_bundle(
            workspace_dir=workspace_dir,
            approval_queue=self.approval_queue,
            log_event=default_log_event,
            run_workflow=self.run_workflow,
            resume_workflow=self.resume_workflow,
        )
        self.workflows_dir = bundle.workflows_dir
        self.definition_runtime = bundle.definition_runtime
        self.lifecycle_runtime = bundle.lifecycle_runtime
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
        if tool_callback is not None:
            self.node_runtime.tool_callback = tool_callback
        if agent_callback is not None:
            self.node_runtime.agent_callback = agent_callback
            self.collaboration_runtime.set_agent_callback(agent_callback)
        if delegate_callback is not None:
            self.collaboration_runtime.set_delegate_callback(delegate_callback)

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
