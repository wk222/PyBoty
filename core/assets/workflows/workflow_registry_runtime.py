"""Runtime registry for active workflows and lightweight workflow views."""

from __future__ import annotations

import threading
from typing import Any

from .workflow_models import NodeStatus, WorkflowDef


class WorkflowRegistryRuntime:
    """Track active workflows separately from orchestration and parsing logic."""

    def __init__(self):
        self._active_workflows: dict[str, WorkflowDef] = {}
        self._lock = threading.Lock()

    def register_active_workflow(self, workflow: WorkflowDef) -> None:
        with self._lock:
            self._active_workflows[workflow.id] = workflow

    def get_active_workflow(self, workflow_id: str) -> WorkflowDef | None:
        with self._lock:
            return self._active_workflows.get(workflow_id)

    def list_active_workflows(self) -> list[dict[str, Any]]:
        with self._lock:
            workflows = list(self._active_workflows.items())

        return [
            {
                "id": workflow_id,
                "name": workflow.name,
                "status": workflow.status.value,
                "nodes_total": len(workflow.nodes),
                "nodes_completed": sum(1 for node in workflow.nodes.values() if node.status == NodeStatus.COMPLETED),
                "nodes_failed": sum(1 for node in workflow.nodes.values() if node.status == NodeStatus.FAILED),
            }
            for workflow_id, workflow in workflows
        ]

    def get_workflow_graph(self, workflow_id: str) -> dict[str, Any] | None:
        workflow = self.get_active_workflow(workflow_id)
        if workflow is None:
            return None
        return {
            "nodes": [node.to_dict() for node in workflow.nodes.values()],
            "edges": [edge.to_dict() for edge in workflow.edges],
            "status": workflow.status.value,
        }
