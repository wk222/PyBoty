"""Lifecycle/runtime facade for workflow persistence, registry, and approvals."""

from __future__ import annotations

import time
from typing import Any

from core.systems.governance.approval_queue import ApprovalQueue

from .workflow_models import WorkflowDef
from .workflow_registry_runtime import WorkflowRegistryRuntime
from .workflow_storage import WorkflowStorage


class WorkflowLifecycleRuntime:
    """Coordinate workflow storage, registry views, and approval listings."""

    def __init__(
        self,
        *,
        storage: WorkflowStorage,
        registry_runtime: WorkflowRegistryRuntime,
        approval_queue: ApprovalQueue,
    ):
        self._storage = storage
        self._registry_runtime = registry_runtime
        self._approval_queue = approval_queue

    def register_active_workflow(self, workflow: WorkflowDef) -> None:
        self._registry_runtime.register_active_workflow(workflow)

    def get_active_workflow(self, workflow_id: str) -> WorkflowDef | None:
        return self._registry_runtime.get_active_workflow(workflow_id)

    def list_active_workflows(self) -> list[dict[str, Any]]:
        return self._registry_runtime.list_active_workflows()

    def get_workflow_graph(self, workflow_id: str) -> dict[str, Any] | None:
        return self._registry_runtime.get_workflow_graph(workflow_id)

    def get_pending_approvals(self) -> list[dict[str, Any]]:
        return self._approval_queue.list_pending(kind="workflow_node")

    def save_workflow_file(self, workflow: WorkflowDef) -> str:
        return self._storage.save_workflow_file(workflow)

    def load_file(self, filepath: str) -> dict[str, Any]:
        return self._storage._load_file(filepath)

    def load_workflow(self, name_or_file: str) -> WorkflowDef | None:
        return self._storage.load_workflow(name_or_file)

    def list_workflow_files(self) -> list[dict[str, Any]]:
        return self._storage.list_workflow_files()

    def save_runtime(self, workflow: WorkflowDef) -> None:
        workflow.updated_at = time.time()
        self._storage.save_runtime(workflow)

    def load_runtime(self, workflow_id: str) -> WorkflowDef | None:
        return self._storage.load_runtime(workflow_id)

    def safe_basename(self, name: str) -> str:
        return self._storage._safe_basename(name)

    def resolve_workflow_path(self, workflow_id: str) -> str:
        return self._storage._resolve_workflow_path(workflow_id)

    def create_workflow_definition(self, name: str, definition: dict[str, Any]) -> str:
        return self._storage.create_workflow_definition(name, definition)

    def update_workflow_definition(self, workflow_id: str, definition: dict[str, Any]) -> str:
        return self._storage.update_workflow_definition(workflow_id, definition)

    def delete_workflow_definition(self, workflow_id: str) -> bool:
        return self._storage.delete_workflow_definition(workflow_id)

    def get_workflow_definition(self, workflow_id: str) -> dict[str, Any]:
        return self._storage.get_workflow_definition(workflow_id)

    # --- Version Control ---

    def publish_workflow(self, workflow_id: str, commit_id: str | None = None) -> dict[str, Any]:
        return self._storage.versions.publish(workflow_id, commit_id)

    def get_workflow_history(self, workflow_id: str, limit: int = 20) -> list[dict[str, Any]]:
        return self._storage.versions.list_history(workflow_id, limit)

    def get_workflow_version(self, workflow_id: str, commit_id: str) -> dict[str, Any] | None:
        return self._storage.versions.get_version(workflow_id, commit_id)

    def rollback_workflow(self, workflow_id: str, commit_id: str) -> dict[str, Any]:
        result = self._storage.versions.rollback(workflow_id, commit_id)
        definition = result.get("definition")
        if definition:
            self._storage.update_workflow_definition(workflow_id, definition)
        return result

    def get_workflow_meta(self, workflow_id: str) -> dict[str, Any]:
        return self._storage.versions.get_meta(workflow_id)
