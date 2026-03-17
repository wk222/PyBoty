from __future__ import annotations

from core.approval_queue import ApprovalQueue
from core.workflow_definition_runtime import WorkflowDefinitionRuntime
from core.workflow_lifecycle_runtime import WorkflowLifecycleRuntime
from core.workflow_models import FlowEdge, FlowNode, NodeStatus, NodeType, WorkflowDef, WorkflowStatus
from core.workflow_registry_runtime import WorkflowRegistryRuntime
from core.workflow_storage import WorkflowStorage


def test_workflow_lifecycle_runtime_manages_definitions_and_runtime_snapshots(tmp_path):
    definition_runtime = WorkflowDefinitionRuntime()
    lifecycle = WorkflowLifecycleRuntime(
        storage=WorkflowStorage(str(tmp_path), definition_runtime.build_workflow),
        registry_runtime=WorkflowRegistryRuntime(),
        approval_queue=ApprovalQueue(),
    )

    workflow = definition_runtime.build_workflow(
        {
            "name": "demo",
            "nodes": [{"id": "step", "type": "exec", "prompt": "hi"}],
        }
    )
    saved_path = lifecycle.save_workflow_file(workflow)
    listed = lifecycle.list_workflow_files()

    assert saved_path.endswith("demo.yml")
    assert listed[0]["name"] == "demo"

    created = lifecycle.create_workflow_definition(
        "ops_flow",
        {"name": "ops_flow", "nodes": [{"id": "step", "type": "exec", "prompt": "run"}]},
    )
    assert created == "ops_flow"
    assert lifecycle.get_workflow_definition("ops_flow")["name"] == "ops_flow"

    workflow.status = WorkflowStatus.RUNNING
    lifecycle.save_runtime(workflow)
    loaded_runtime = lifecycle.load_runtime(workflow.id)

    assert loaded_runtime is not None
    assert loaded_runtime.id == workflow.id
    assert loaded_runtime.status == WorkflowStatus.RUNNING


def test_workflow_lifecycle_runtime_surfaces_registry_and_pending_approvals(tmp_path):
    queue = ApprovalQueue()
    lifecycle = WorkflowLifecycleRuntime(
        storage=WorkflowStorage(str(tmp_path), WorkflowDefinitionRuntime().build_workflow),
        registry_runtime=WorkflowRegistryRuntime(),
        approval_queue=queue,
    )
    workflow = WorkflowDef(
        id="wf_live",
        name="live",
        status=WorkflowStatus.RUNNING,
        nodes={
            "start": FlowNode(id="start", type=NodeType.START, status=NodeStatus.COMPLETED),
            "step": FlowNode(id="step", type=NodeType.EXEC, status=NodeStatus.RUNNING),
            "end": FlowNode(id="end", type=NodeType.END, status=NodeStatus.PENDING),
        },
        edges=[
            FlowEdge(id="e1", source="start", target="step"),
            FlowEdge(id="e2", source="step", target="end"),
        ],
    )

    lifecycle.register_active_workflow(workflow)
    request = queue.create_request(
        kind="workflow_node",
        scope="workflow:wf_live",
        summary="approve live step",
        prompt="continue?",
    )

    assert lifecycle.list_active_workflows()[0]["id"] == "wf_live"
    assert lifecycle.get_workflow_graph("wf_live") is not None
    assert lifecycle.get_pending_approvals()[0]["approval_id"] == request.approval_id
