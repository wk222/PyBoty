from __future__ import annotations

from core.workflow_graph_runtime import WorkflowGraphRuntime
from core.workflow_models import FlowEdge, FlowNode, NodeStatus, NodeType, WorkflowDef


def test_workflow_graph_runtime_resolves_config_and_ready_nodes():
    runtime = WorkflowGraphRuntime()
    workflow = WorkflowDef(
        id="wf_graph",
        name="graph",
        variables={"user.name": "alice", "flag": True, "payload": {"x": 1}},
        nodes={
            "start": FlowNode(id="start", type=NodeType.START, status=NodeStatus.COMPLETED),
            "task": FlowNode(id="task", type=NodeType.EXEC, status=NodeStatus.PENDING),
            "end": FlowNode(id="end", type=NodeType.END, status=NodeStatus.PENDING),
        },
        edges=[
            FlowEdge(id="e1", source="start", target="task"),
            FlowEdge(id="e2", source="task", target="end"),
        ],
    )

    resolved = runtime.resolve_config(
        {"message": "hello ${user.name}", "enabled": "${flag}", "payload": "${payload}"},
        workflow,
    )

    assert resolved["message"] == "hello alice"
    assert resolved["enabled"] is True
    assert resolved["payload"] == {"x": 1}
    assert runtime.get_ready_nodes(workflow) == ["task"]


def test_workflow_graph_runtime_detects_cycles():
    runtime = WorkflowGraphRuntime()
    workflow = WorkflowDef(
        id="wf_cycle",
        name="cycle",
        nodes={
            "a": FlowNode(id="a", type=NodeType.EXEC),
            "b": FlowNode(id="b", type=NodeType.EXEC),
        },
        edges=[
            FlowEdge(id="e1", source="a", target="b"),
            FlowEdge(id="e2", source="b", target="a"),
        ],
    )

    try:
        runtime.topo_sort(workflow)
    except ValueError as exc:
        assert "循环依赖" in str(exc)
    else:
        raise AssertionError("Expected topo_sort to reject cyclic workflow")
