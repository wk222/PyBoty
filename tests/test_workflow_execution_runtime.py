from __future__ import annotations

from core.systems.governance.approval_queue import ApprovalQueue
from core.assets.workflows.execution import WorkflowExecutionRuntime
from core.assets.workflows.models import (
    FlowEdge,
    FlowNode,
    NodeStatus,
    NodeType,
    WorkflowApprovalPause,
    WorkflowDef,
    WorkflowStatus,
)
from core.systems.runtime import SessionRuntime


def test_workflow_execution_runtime_returns_waiting_approval_payload():
    queue = ApprovalQueue()
    workflow = WorkflowDef(
        id="wf_pause",
        name="paused",
        nodes={"gate": FlowNode(id="gate", type=NodeType.APPROVE, label="Gate")},
    )
    runtime = _build_runtime(
        approval_queue=queue,
        workflows={"wf_pause": workflow},
        exec_node=lambda node, workflow, run_id: _raise_pause(node, workflow),
    )

    result = runtime.run_workflow(workflow)

    assert result["status"] == "waiting_approval"
    assert result["workflow_id"] == "wf_pause"
    assert result["node_id"] == "gate"


def test_workflow_execution_runtime_resumes_approved_node_and_finalizes_queue():
    queue = ApprovalQueue()
    workflow = WorkflowDef(
        id="wf_resume",
        name="resume-demo",
        status=WorkflowStatus.PAUSED,
        resume_token="resume-123",
        nodes={"gate": FlowNode(id="gate", type=NodeType.APPROVE, status=NodeStatus.WAITING, label="Gate")},
    )
    queue.create_request(
        kind="workflow_node",
        scope="workflow:wf_resume",
        summary="gate approval",
        prompt="allow?",
        fingerprint="wf_resume:gate:resume-123",
    )
    runtime = _build_runtime(
        approval_queue=queue,
        workflows={"wf_resume": workflow},
        exec_node=lambda node, workflow, run_id: {"ok": True},
    )

    result = runtime.resume_workflow("wf_resume", "resume-123", True)

    assert result["status"] == "completed"
    assert workflow.nodes["gate"].status == NodeStatus.COMPLETED
    request = queue.find_resolution(
        kind="workflow_node",
        scope="workflow:wf_resume",
        fingerprint="wf_resume:gate:resume-123",
    )
    assert request is not None
    assert request.status == "approved"


def test_workflow_execution_runtime_projects_runs_into_session_timeline(tmp_path):
    queue = ApprovalQueue()
    session_runtime = SessionRuntime(tmp_path / "sessions.json")
    workflow = WorkflowDef(
        id="wf_timeline",
        name="timeline-demo",
        variables={"thread_id": "thread-workflow", "session_key": "session-workflow", "root_mode": "admin"},
        nodes={"exec": FlowNode(id="exec", type=NodeType.EXEC, label="Exec")},
    )
    runtime = _build_runtime(
        approval_queue=queue,
        workflows={"wf_timeline": workflow},
        exec_node=lambda node, workflow, run_id: _complete_node(node, {"ok": True, "run_id": run_id}),
        session_runtime=session_runtime,
    )

    result = runtime.run_workflow(workflow)

    assert result["status"] == "completed"
    assert result["run_id"]
    session = session_runtime.get_session("session-workflow")
    assert session is not None
    assert session["timeline"][-1]["kind"] == "workflow_run"
    assert session["timeline"][-1]["status"] == "completed"
    assert session["timeline"][-1]["run_id"] == result["run_id"]


def test_workflow_execution_runtime_skips_unselected_condition_branch():
    workflow = WorkflowDef(
        id="wf_condition",
        name="condition-demo",
        nodes={
            "route": FlowNode(id="route", type=NodeType.CONDITION),
            "yes": FlowNode(id="yes", type=NodeType.EXEC),
            "no": FlowNode(id="no", type=NodeType.EXEC),
        },
        edges=[
            FlowEdge(id="e1", source="route", target="yes"),
            FlowEdge(id="e2", source="route", target="no"),
        ],
    )

    def exec_node(node: FlowNode, workflow: WorkflowDef, run_id: str):
        node.status = NodeStatus.COMPLETED
        node.output = node.id
        if node.id == "route":
            return {"_branch": "yes"}
        return {"done": node.id}

    runtime = _build_runtime(
        approval_queue=ApprovalQueue(),
        workflows={"wf_condition": workflow},
        exec_node=exec_node,
    )

    result = runtime.run_workflow(workflow)

    assert result["status"] == "completed"
    assert workflow.nodes["yes"].status == NodeStatus.COMPLETED
    assert workflow.nodes["no"].status == NodeStatus.SKIPPED


def test_workflow_execution_runtime_resumes_waiting_delegated_agent_node():
    queue = ApprovalQueue()
    workflow = WorkflowDef(
        id="wf_delegate",
        name="delegate-demo",
        status=WorkflowStatus.PAUSED,
        resume_token="resume-delegate",
        nodes={
            "delegate": FlowNode(
                id="delegate",
                type=NodeType.AGENT,
                status=NodeStatus.WAITING,
                label="Helper",
                output={
                    "status": "waiting_approval",
                    "approval_id": "appr_delegate",
                    "workflow_pause_kind": "delegated_subagent",
                    "workflow_pause_mode": "agent",
                    "agent_name": "helper",
                    "task": "完成任务",
                },
            ),
            "end": FlowNode(id="end", type=NodeType.END),
        },
        edges=[FlowEdge(id="e1", source="delegate", target="end")],
    )
    request = queue.create_request(
        kind="tool_call",
        scope="subagent:helper",
        summary="helper approval",
        prompt="allow helper?",
        callback=lambda approved, note: {
            "status": "completed",
            "success": approved,
            "response": "helper done" if approved else note,
            "thread_id": "delegate-thread",
            "state_update": {"next_step": "ship"},
        },
    )
    queue.resolve(
        request.approval_id,
        approved=True,
        note="ok",
        resolved_by="lead",
    )
    workflow.nodes["delegate"].output["approval_id"] = request.approval_id

    runtime = _build_runtime(
        approval_queue=queue,
        workflows={"wf_delegate": workflow},
        exec_node=lambda node, workflow, run_id: _complete_node(node, {"final": True}),
    )

    result = runtime.resume_workflow("wf_delegate", "resume-delegate", True, note="ok", resolved_by="lead")

    assert result["status"] == "completed"
    assert workflow.nodes["delegate"].status == NodeStatus.COMPLETED
    assert workflow.nodes["delegate"].output["response"] == "helper done"
    assert workflow.nodes["delegate"].output["approval_resolved_by"] == "lead"
    assert workflow.nodes["delegate"].output["state_update"] == {"next_step": "ship"}


def test_workflow_execution_runtime_requeues_delegated_agent_when_followup_approval_is_needed():
    queue = ApprovalQueue()
    next_request = queue.create_request(
        kind="tool_call",
        scope="subagent:helper",
        summary="second approval",
        prompt="allow second?",
    )
    first_request = queue.create_request(
        kind="tool_call",
        scope="subagent:helper",
        summary="first approval",
        prompt="allow first?",
        callback=lambda approved, note: {
            "status": "waiting_approval",
            "success": False,
            "response": "need another approval",
            "approval_id": next_request.approval_id,
            "thread_id": "delegate-thread",
        },
    )
    workflow = WorkflowDef(
        id="wf_delegate_retry",
        name="delegate-retry",
        status=WorkflowStatus.PAUSED,
        resume_token="resume-delegate",
        nodes={
            "delegate": FlowNode(
                id="delegate",
                type=NodeType.AGENT,
                status=NodeStatus.WAITING,
                label="Helper",
                output={
                    "status": "waiting_approval",
                    "approval_id": first_request.approval_id,
                    "workflow_pause_kind": "delegated_subagent",
                    "workflow_pause_mode": "agent",
                    "agent_name": "helper",
                    "task": "完成任务",
                },
            )
        },
    )
    queue.resolve(first_request.approval_id, approved=True, note="go")

    runtime = _build_runtime(
        approval_queue=queue,
        workflows={"wf_delegate_retry": workflow},
        exec_node=lambda node, workflow, run_id: {"final": True},
    )

    result = runtime.resume_workflow("wf_delegate_retry", "resume-delegate", True)

    assert result["status"] == "waiting_approval"
    assert result["approval_id"] == next_request.approval_id
    assert workflow.nodes["delegate"].status == NodeStatus.WAITING
    assert next_request.metadata["workflow_id"] == "wf_delegate_retry"


def test_workflow_execution_runtime_resumes_waiting_debate_node_via_collaboration_runtime():
    queue = ApprovalQueue()
    request = queue.create_request(
        kind="tool_call",
        scope="subagent:pro",
        summary="debate approval",
        prompt="allow pro?",
    )
    queue.resolve(request.approval_id, approved=True, note="ok")
    workflow = WorkflowDef(
        id="wf_debate",
        name="debate",
        status=WorkflowStatus.PAUSED,
        resume_token="resume-debate",
        nodes={
            "debate": FlowNode(
                id="debate",
                type=NodeType.DEBATE,
                status=NodeStatus.WAITING,
                label="Debate",
                output={
                    "status": "waiting_approval",
                    "approval_id": request.approval_id,
                    "workflow_pause_kind": "delegated_subagent",
                    "workflow_pause_mode": "debate",
                    "workflow_pause_state": {"step_index": 0, "history": "辩论主题: 是否上线\n\n", "transcript": []},
                },
            )
        },
    )

    runtime = _build_runtime(
        approval_queue=queue,
        workflows={"wf_debate": workflow},
        exec_node=lambda node, workflow, run_id: _complete_node(node, {"final": True}),
        resume_collaboration_node=lambda node, workflow, waiting_payload, resolved_payload: {
            "topic": "是否上线",
            "transcript": [{"agent": "pro", "content": "pro-response"}],
            "conclusion": "judge-response",
            "response": "judge-response",
        },
    )

    result = runtime.resume_workflow("wf_debate", "resume-debate", True)

    assert result["status"] == "completed"
    assert workflow.nodes["debate"].status == NodeStatus.COMPLETED
    assert workflow.nodes["debate"].output["conclusion"] == "judge-response"


def test_workflow_execution_runtime_requeues_waiting_consensus_node_with_updated_pause_state():
    queue = ApprovalQueue()
    next_request = queue.create_request(
        kind="tool_call",
        scope="subagent:reviewer",
        summary="consensus approval",
        prompt="allow reviewer?",
    )
    first_request = queue.create_request(
        kind="tool_call",
        scope="subagent:planner",
        summary="planner approval",
        prompt="allow planner?",
    )
    queue.resolve(first_request.approval_id, approved=True, note="ok")
    workflow = WorkflowDef(
        id="wf_consensus",
        name="consensus",
        status=WorkflowStatus.PAUSED,
        resume_token="resume-consensus",
        nodes={
            "consensus": FlowNode(
                id="consensus",
                type=NodeType.CONSENSUS,
                status=NodeStatus.WAITING,
                label="Consensus",
                output={
                    "status": "waiting_approval",
                    "approval_id": first_request.approval_id,
                    "workflow_pause_kind": "delegated_subagent",
                    "workflow_pause_mode": "consensus",
                    "workflow_pause_state": {"phase": "experts", "next_agent_index": 0},
                },
            )
        },
    )

    runtime = _build_runtime(
        approval_queue=queue,
        workflows={"wf_consensus": workflow},
        exec_node=lambda node, workflow, run_id: _complete_node(node, {"final": True}),
        resume_collaboration_node=lambda node, workflow, waiting_payload, resolved_payload: {
            "status": "waiting_approval",
            "approval_id": next_request.approval_id,
            "response": "need reviewer approval",
            "workflow_pause_kind": "delegated_subagent",
            "workflow_pause_mode": "consensus",
            "workflow_pause_state": {
                "phase": "experts",
                "next_agent_index": 1,
                "responses": {"planner": "planner-answer"},
                "delegation_results": {"planner": {"response": "planner-answer"}},
            },
        },
    )

    result = runtime.resume_workflow("wf_consensus", "resume-consensus", True)

    assert result["status"] == "waiting_approval"
    assert result["approval_id"] == next_request.approval_id
    assert workflow.nodes["consensus"].output["workflow_pause_state"]["next_agent_index"] == 1


def test_workflow_execution_runtime_resumes_specific_pending_consensus_approval_and_keeps_other_experts_waiting():
    queue = ApprovalQueue()
    reviewer_request = queue.create_request(
        kind="tool_call",
        scope="subagent:reviewer",
        summary="reviewer approval",
        prompt="allow reviewer?",
        callback=lambda approved, note: {
            "status": "completed",
            "success": approved,
            "response": "reviewer" if approved else note,
        },
    )
    security_request = queue.create_request(
        kind="tool_call",
        scope="subagent:security",
        summary="security approval",
        prompt="allow security?",
    )
    queue.resolve(reviewer_request.approval_id, approved=True, note="ok")
    workflow = WorkflowDef(
        id="wf_consensus_multi",
        name="consensus-multi",
        status=WorkflowStatus.PAUSED,
        resume_token="resume-consensus-multi",
        nodes={
            "consensus": FlowNode(
                id="consensus",
                type=NodeType.CONSENSUS,
                status=NodeStatus.WAITING,
                label="Consensus",
                output={
                    "status": "waiting_approval",
                    "approval_id": reviewer_request.approval_id,
                    "approval_ids": [reviewer_request.approval_id, security_request.approval_id],
                    "pending_approvals": [
                        {
                            "agent_name": "reviewer",
                            "task": "如何发布",
                            "approval_id": reviewer_request.approval_id,
                        },
                        {
                            "agent_name": "security",
                            "task": "如何发布",
                            "approval_id": security_request.approval_id,
                        },
                    ],
                    "workflow_pause_kind": "delegated_subagent",
                    "workflow_pause_mode": "consensus",
                    "workflow_pause_state": {
                        "phase": "experts",
                        "next_agent_index": 3,
                        "responses": {"planner": "planner-answer"},
                        "delegation_results": {"planner": {"response": "planner-answer"}},
                        "pending_approvals": [
                            {
                                "agent_name": "reviewer",
                                "task": "如何发布",
                                "approval_id": reviewer_request.approval_id,
                            },
                            {
                                "agent_name": "security",
                                "task": "如何发布",
                                "approval_id": security_request.approval_id,
                            },
                        ],
                    },
                },
            )
        },
    )

    runtime = _build_runtime(
        approval_queue=queue,
        workflows={"wf_consensus_multi": workflow},
        exec_node=lambda node, workflow, run_id: _complete_node(node, {"final": True}),
        resume_collaboration_node=lambda node, workflow, waiting_payload, resolved_payload, resolved_approval_id: {
            "status": "waiting_approval",
            "approval_id": security_request.approval_id,
            "approval_ids": [security_request.approval_id],
            "pending_approvals": [
                {
                    "agent_name": "security",
                    "task": "如何发布",
                    "approval_id": security_request.approval_id,
                }
            ],
            "response": "security still pending",
            "workflow_pause_kind": "delegated_subagent",
            "workflow_pause_mode": "consensus",
            "workflow_pause_state": {
                "phase": "experts",
                "next_agent_index": 3,
                "responses": {
                    "planner": "planner-answer",
                    "reviewer": resolved_payload["response"],
                },
                "delegation_results": {
                    "planner": {"response": "planner-answer"},
                    "reviewer": {"response": resolved_payload["response"]},
                },
                "pending_approvals": [
                    {
                        "agent_name": "security",
                        "task": "如何发布",
                        "approval_id": security_request.approval_id,
                    }
                ],
                "resolved_approval_id": resolved_approval_id,
            },
        },
    )

    result = runtime.resume_workflow(
        "wf_consensus_multi",
        "resume-consensus-multi",
        True,
        approval_id=reviewer_request.approval_id,
    )

    assert result["status"] == "waiting_approval"
    assert result["approval_id"] == security_request.approval_id
    assert workflow.nodes["consensus"].output["pending_approvals"][0]["agent_name"] == "security"
    assert workflow.nodes["consensus"].output["workflow_pause_state"]["responses"]["reviewer"] == "reviewer"


def _build_runtime(
    *,
    approval_queue: ApprovalQueue,
    workflows: dict[str, WorkflowDef],
    exec_node,
    resume_collaboration_node=None,
    session_runtime=None,
):
    saved: list[tuple[str, str]] = []

    def topo_sort(workflow: WorkflowDef) -> list[str]:
        return list(workflow.nodes)

    def get_ready_nodes(workflow: WorkflowDef) -> list[str]:
        ready = []
        for node_id, node in workflow.nodes.items():
            if node.status != NodeStatus.PENDING:
                continue
            predecessors = [edge.source for edge in workflow.edges if edge.target == node_id]
            if all(workflow.nodes[pred].status in {NodeStatus.COMPLETED, NodeStatus.SKIPPED} for pred in predecessors):
                ready.append(node_id)
        return ready

    def get_successors(workflow: WorkflowDef, node_id: str) -> list[FlowEdge]:
        return [edge for edge in workflow.edges if edge.source == node_id]

    def save_workflow(workflow: WorkflowDef) -> None:
        saved.append((workflow.id, workflow.status.value))

    wrapped_resume_collaboration = None
    if resume_collaboration_node is not None:

        def wrapped_resume_collaboration(node, workflow, waiting_payload, resolved_payload, resolved_approval_id=""):
            try:
                return resume_collaboration_node(
                    node,
                    workflow,
                    waiting_payload,
                    resolved_payload,
                    resolved_approval_id,
                )
            except TypeError:
                return resume_collaboration_node(node, workflow, waiting_payload, resolved_payload)

    return WorkflowExecutionRuntime(
        approval_queue=approval_queue,
        topo_sort=topo_sort,
        get_ready_nodes=get_ready_nodes,
        get_successors=get_successors,
        exec_node=exec_node,
        resolve_var=lambda value, workflow: (
            workflow.variables.get(value[2:-1], value)
            if isinstance(value, str) and value.startswith("${") and value.endswith("}")
            else value
        ),
        register_active_workflow=lambda workflow: workflows.__setitem__(workflow.id, workflow),
        save_workflow=save_workflow,
        load_workflow=lambda workflow_id: workflows.get(workflow_id),
        get_active_workflow=lambda workflow_id: workflows.get(workflow_id),
        workflow_approval_fingerprint=lambda **kwargs: (
            f"{kwargs['workflow_id']}:{kwargs['node_id']}:{kwargs['resume_token']}"
        ),
        log_event=lambda workflow, node_id, event, detail="": None,
        session_runtime=session_runtime,
        resume_collaboration_node=wrapped_resume_collaboration,
    )


def _raise_pause(node: FlowNode, workflow: WorkflowDef):
    node.status = NodeStatus.WAITING
    workflow.status = WorkflowStatus.PAUSED
    workflow.resume_token = "resume-token"
    raise WorkflowApprovalPause(
        workflow_id=workflow.id,
        node_id=node.id,
        resume_token="resume-token",
        prompt="continue?",
        approval_id="appr_demo",
    )


def _complete_node(node: FlowNode, output):
    node.status = NodeStatus.COMPLETED
    node.output = output
    return output
