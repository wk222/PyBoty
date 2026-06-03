from __future__ import annotations

from core.systems.governance.approval_queue import ApprovalQueue
from core.assets.workflows.workflow_engine_factory import build_workflow_engine_runtime_bundle


def test_workflow_engine_factory_builds_shared_runtime_bundle(tmp_path):
    queue = ApprovalQueue()
    events: list[tuple[str, str, str]] = []
    tool_callback = lambda tool, args: {"tool": tool, "args": args}
    agent_callback = lambda prompt: f"agent:{prompt}"
    delegate_callback = lambda agent, task, context: {"agent": agent, "task": task, "context": context}

    bundle = build_workflow_engine_runtime_bundle(
        workspace_dir=str(tmp_path / "workspace"),
        approval_queue=queue,
        log_event=lambda workflow, node_id, event, detail="": events.append((workflow.id, node_id, event)),
    )
    bundle.bind_engine_callbacks(
        run_workflow=lambda workflow: {"status": "completed", "workflow_id": workflow.id},
        resume_workflow=lambda workflow_id, resume_token, approved, **kwargs: {
            "status": "completed",
            "workflow_id": workflow_id,
            "approved": approved,
        },
    )
    bundle.configure_callbacks(
        tool_callback=tool_callback,
        agent_callback=agent_callback,
        delegate_callback=delegate_callback,
    )

    assert bundle.workflows_dir.endswith("workflows")
    assert bundle.lifecycle_runtime.get_pending_approvals() == []
    assert bundle.execution_runtime.approval_queue is queue
    assert bundle.node_runtime.approval_queue is queue
    assert bundle.collaboration_runtime is not None
    assert bundle.node_runtime.run_workflow is not None
    assert bundle.node_runtime.resume_workflow is not None
    assert bundle.node_runtime.tool_callback is tool_callback
    assert bundle.node_runtime.agent_callback is agent_callback
    assert bundle.collaboration_runtime.agent_callback is agent_callback
    assert bundle.collaboration_runtime.delegate_callback is delegate_callback
