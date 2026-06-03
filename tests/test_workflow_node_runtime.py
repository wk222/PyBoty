from __future__ import annotations

import pytest

from core.systems.governance.approval_queue import ApprovalQueue
from core.assets.workflows.workflow_models import FlowNode, NodeType, WorkflowDef, WorkflowStatus
from core.assets.workflows.workflow_node_runtime import WorkflowApprovalPause, WorkflowNodeRuntime


def test_workflow_node_runtime_executes_tool_nodes_and_updates_state():
    runtime = _build_runtime(tool_callback=lambda tool_name, args: {"tool": tool_name, "args": args})
    workflow = WorkflowDef(id="wf_tool", name="tool-demo")
    node = FlowNode(id="call_tool", type=NodeType.TOOL, config={"tool": "lookup", "args": {"q": "hi"}})

    result = runtime.exec_node(node, workflow)

    assert result == {"tool": "lookup", "args": {"q": "hi"}}
    assert node.status.value == "completed"
    assert workflow.variables["call_tool.status"] == "completed"
    assert workflow.variables["call_tool.output"] == result


def test_workflow_node_runtime_approve_node_creates_pause_request():
    saved = []
    queue = ApprovalQueue()
    runtime = _build_runtime(
        approval_queue=queue,
        save_workflow=lambda workflow: saved.append((workflow.id, workflow.status.value)),
    )
    workflow = WorkflowDef(id="wf_approve", name="approve-demo")
    node = FlowNode(id="review", type=NodeType.APPROVE, label="人工审核", config={"prompt": "继续执行吗？"})

    with pytest.raises(WorkflowApprovalPause) as exc_info:
        runtime.exec_node(node, workflow)

    assert workflow.status == WorkflowStatus.PAUSED
    assert node.status.value == "waiting"
    assert saved == [("wf_approve", "paused")]
    pending = queue.list_pending(kind="workflow_node")
    assert len(pending) == 1
    assert pending[0]["metadata"]["node_id"] == "review"
    assert exc_info.value.approval_id == pending[0]["approval_id"]


def test_workflow_node_runtime_delegated_pause_attaches_shared_resume_metadata():
    saved = []
    queue = ApprovalQueue()
    request = queue.create_request(
        kind="tool_call",
        scope="subagent:helper",
        summary="helper approval",
        prompt="allow helper?",
    )
    runtime = _build_runtime(
        approval_queue=queue,
        save_workflow=lambda workflow: saved.append((workflow.id, workflow.status.value)),
        extra_dispatch=lambda node, config, workflow: {
            "status": "waiting_approval",
            "approval_id": request.approval_id,
            "workflow_pause_kind": "delegated_subagent",
            "workflow_pause_mode": "agent",
            "agent_name": "helper",
            "task": "完成任务",
        },
    )
    workflow = WorkflowDef(id="wf_delegate", name="delegate-demo")
    node = FlowNode(id="delegate", type=NodeType.AGENT, label="Helper", config={"agent_name": "helper", "task": "完成任务"})

    with pytest.raises(WorkflowApprovalPause) as exc_info:
        runtime.exec_node(node, workflow)

    assert workflow.status == WorkflowStatus.PAUSED
    assert saved == [("wf_delegate", "paused")]
    assert exc_info.value.approval_id == request.approval_id
    assert request.metadata["workflow_id"] == "wf_delegate"
    assert request.metadata["workflow_pause_mode"] == "agent"
    assert workflow.variables["delegate.approval_id"] == request.approval_id


def _build_runtime(
    *,
    approval_queue: ApprovalQueue | None = None,
    save_workflow=None,
    tool_callback=None,
    extra_dispatch=None,
):
    queue = approval_queue or ApprovalQueue()
    return WorkflowNodeRuntime(
        workspace_dir="workspace",
        approval_queue=queue,
        save_workflow=save_workflow or (lambda workflow: None),
        load_workflow=lambda workflow_id: None,
        resume_workflow=lambda workflow_id, resume_token, approved: {"approved": approved},
        run_workflow=lambda workflow: {"status": "completed"},
        resolve_var=lambda value, workflow: value,
        resolve_config=lambda config, workflow: config,
        evaluate_condition=lambda condition, workflow: str(condition).strip().lower() in {"true", "1", "yes"},
        get_predecessors=lambda workflow, node_id: [],
        workflow_approval_fingerprint=lambda **kwargs: (
            f"{kwargs['workflow_id']}:{kwargs['node_id']}:{kwargs['resume_token']}"
        ),
        log_event=lambda workflow, node_id, event, detail="": None,
        extra_dispatch=extra_dispatch or (lambda node, config, workflow: {"node_type": node.type.value, "config": config}),
        tool_callback=tool_callback,
        agent_callback=lambda prompt: prompt,
    )
