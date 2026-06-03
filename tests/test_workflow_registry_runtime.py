from __future__ import annotations

from core.assets.workflows.workflow_models import FlowEdge, FlowNode, NodeStatus, NodeType, WorkflowDef, WorkflowStatus
from core.assets.workflows.workflow_registry_runtime import WorkflowRegistryRuntime


def test_workflow_registry_runtime_lists_active_workflows_and_graphs():
    runtime = WorkflowRegistryRuntime()
    workflow = WorkflowDef(
        id="wf_active",
        name="active-demo",
        status=WorkflowStatus.RUNNING,
        nodes={
            "start": FlowNode(id="start", type=NodeType.START, status=NodeStatus.COMPLETED),
            "task": FlowNode(id="task", type=NodeType.EXEC, status=NodeStatus.FAILED),
            "end": FlowNode(id="end", type=NodeType.END, status=NodeStatus.PENDING),
        },
        edges=[
            FlowEdge(id="e1", source="start", target="task"),
            FlowEdge(id="e2", source="task", target="end"),
        ],
    )

    runtime.register_active_workflow(workflow)

    listing = runtime.list_active_workflows()
    graph = runtime.get_workflow_graph("wf_active")

    assert listing == [
        {
            "id": "wf_active",
            "name": "active-demo",
            "status": "running",
            "nodes_total": 3,
            "nodes_completed": 1,
            "nodes_failed": 1,
        }
    ]
    assert graph is not None
    assert graph["status"] == "running"
    assert len(graph["nodes"]) == 3
    assert len(graph["edges"]) == 2


def test_workflow_registry_runtime_returns_none_for_unknown_workflow():
    runtime = WorkflowRegistryRuntime()

    assert runtime.get_workflow_graph("missing") is None
