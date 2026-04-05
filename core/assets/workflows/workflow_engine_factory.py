"""Factory helpers for assembling the workflow engine runtime bundle."""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any

from core.systems.governance.approval_queue import ApprovalQueue

from .workflow_collaboration_runtime import WorkflowCollaborationRuntime
from .workflow_definition_runtime import WorkflowDefinitionRuntime
from .workflow_execution_runtime import WorkflowExecutionRuntime
from .workflow_graph_runtime import WorkflowGraphRuntime
from .workflow_lifecycle_runtime import WorkflowLifecycleRuntime
from .workflow_node_runtime import WorkflowNodeRuntime
from .workflow_registry_runtime import WorkflowRegistryRuntime
from .workflow_storage import WorkflowStorage


@dataclass
class WorkflowEngineRuntimeBundle:
    """Structured runtime bundle behind ``PyFlowEngine``."""

    definition_runtime: WorkflowDefinitionRuntime
    lifecycle_runtime: WorkflowLifecycleRuntime
    graph_runtime: WorkflowGraphRuntime
    collaboration_runtime: WorkflowCollaborationRuntime
    execution_runtime: WorkflowExecutionRuntime
    node_runtime: WorkflowNodeRuntime
    workflows_dir: str

    def bind_engine_callbacks(
        self,
        *,
        run_workflow: Any | None = None,
        resume_workflow: Any | None = None,
    ) -> None:
        """Bind engine methods after construction to avoid circular setup."""
        self.node_runtime.bind_engine_callbacks(
            run_workflow=run_workflow,
            resume_workflow=resume_workflow,
        )

    def configure_callbacks(
        self,
        *,
        tool_callback: Any | None = None,
        agent_callback: Any | None = None,
        delegate_callback: Any | None = None,
    ) -> None:
        """Wire tool/agent/delegate callbacks into the shared runtimes."""
        if tool_callback is not None:
            self.node_runtime.tool_callback = tool_callback
        if agent_callback is not None:
            self.node_runtime.agent_callback = agent_callback
            self.collaboration_runtime.set_agent_callback(agent_callback)
        if delegate_callback is not None:
            self.collaboration_runtime.set_delegate_callback(delegate_callback)


def build_workflow_engine_runtime_bundle(
    *,
    workspace_dir: str,
    approval_queue: ApprovalQueue,
    log_event: Any,
    run_workflow: Any | None = None,
    resume_workflow: Any | None = None,
    session_runtime: Any | None = None,
) -> WorkflowEngineRuntimeBundle:
    """Assemble workflow runtimes with shared storage and orchestration callbacks."""

    workflows_dir = os.path.join(workspace_dir, "workflows")
    definition_runtime = WorkflowDefinitionRuntime()
    lifecycle_runtime = WorkflowLifecycleRuntime(
        storage=WorkflowStorage(workflows_dir, definition_runtime.build_workflow),
        registry_runtime=WorkflowRegistryRuntime(),
        approval_queue=approval_queue,
    )
    graph_runtime = WorkflowGraphRuntime()
    collaboration_runtime = WorkflowCollaborationRuntime(log_event=log_event)
    node_runtime = WorkflowNodeRuntime(
        workspace_dir=workspace_dir,
        approval_queue=approval_queue,
        save_workflow=lifecycle_runtime.save_runtime,
        load_workflow=lifecycle_runtime.load_workflow,
        resume_workflow=None,
        run_workflow=None,
        resolve_var=graph_runtime.resolve_var,
        resolve_config=graph_runtime.resolve_config,
        evaluate_condition=graph_runtime.evaluate_condition,
        get_predecessors=graph_runtime.get_predecessors,
        workflow_approval_fingerprint=_workflow_approval_fingerprint,
        log_event=log_event,
        extra_dispatch=collaboration_runtime.dispatch_node,
    )
    execution_runtime = WorkflowExecutionRuntime(
        approval_queue=approval_queue,
        topo_sort=graph_runtime.topo_sort,
        get_ready_nodes=graph_runtime.get_ready_nodes,
        get_successors=graph_runtime.get_successors,
        exec_node=node_runtime.exec_node,
        resolve_var=graph_runtime.resolve_var,
        register_active_workflow=lifecycle_runtime.register_active_workflow,
        save_workflow=lifecycle_runtime.save_runtime,
        load_workflow=lifecycle_runtime.load_runtime,
        get_active_workflow=lifecycle_runtime.get_active_workflow,
        workflow_approval_fingerprint=_workflow_approval_fingerprint,
        log_event=log_event,
        session_runtime=session_runtime,
        process_node_success=graph_runtime.process_node_success,
        reset_edge_states=graph_runtime.reset_edge_states,
        resume_collaboration_node=collaboration_runtime.resume_delegated_node,
    )
    bundle = WorkflowEngineRuntimeBundle(
        definition_runtime=definition_runtime,
        lifecycle_runtime=lifecycle_runtime,
        graph_runtime=graph_runtime,
        collaboration_runtime=collaboration_runtime,
        execution_runtime=execution_runtime,
        node_runtime=node_runtime,
        workflows_dir=workflows_dir,
    )
    bundle.bind_engine_callbacks(
        run_workflow=run_workflow,
        resume_workflow=resume_workflow,
    )
    return bundle


def _workflow_approval_fingerprint(*, workflow_id: str, node_id: str, resume_token: str) -> str:
    return f"{workflow_id}:{node_id}:{resume_token}"


def default_log_event(workflow: Any, node_id: str, event: str, detail: str = "") -> None:
    """Append a timestamped entry to the workflow execution log."""
    import time as _time

    workflow.execution_log.append({"time": _time.time(), "node": node_id, "event": event, "detail": detail[:500]})
