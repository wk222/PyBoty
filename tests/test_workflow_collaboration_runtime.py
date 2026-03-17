from __future__ import annotations

from core.workflow_collaboration_runtime import WorkflowCollaborationRuntime
from core.workflow_models import FlowNode, NodeType, WorkflowDef


def test_workflow_collaboration_runtime_runs_debate_and_uses_root_judge():
    events = []

    runtime = WorkflowCollaborationRuntime(
        log_event=lambda workflow, node_id, event, detail="": events.append((node_id, event, detail)),
        agent_callback=lambda prompt: "综合结论",
        delegate_callback=lambda agent, task, context: f"{agent}:{task[:12]}",
    )
    workflow = WorkflowDef(id="wf_debate", name="debate")
    node = FlowNode(
        id="debate_1",
        type=NodeType.DEBATE,
        config={"topic": "是否上线", "agent_a": "pro", "agent_b": "con", "rounds": 1},
    )

    result = runtime.dispatch_node(node, node.config, workflow)

    assert result["conclusion"] == "综合结论"
    assert len(result["transcript"]) == 2
    assert events[0][1] == "debate_round"
    assert events[-1][1] == "debate_judge"


def test_workflow_collaboration_runtime_debate_resumes_without_rerunning_completed_turn():
    calls = []
    runtime = WorkflowCollaborationRuntime(
        log_event=lambda workflow, node_id, event, detail="": None,
        agent_callback=lambda prompt: "unused",
        delegate_callback=lambda agent, task, context: (
            calls.append(agent)
            or {
                "status": "completed",
                "success": True,
                "response": f"{agent}-response",
                "thread_id": f"thread-{agent}",
            }
        ),
    )
    workflow = WorkflowDef(id="wf_debate_resume", name="debate")
    node = FlowNode(
        id="debate_1",
        type=NodeType.DEBATE,
        config={"topic": "是否上线", "agent_a": "pro", "agent_b": "con", "judge": "judge", "rounds": 1},
    )

    result = runtime.resume_delegated_node(
        node=node,
        workflow=workflow,
        waiting_payload={
            "workflow_pause_mode": "debate",
            "workflow_pause_state": {
                "step_index": 0,
                "history": "辩论主题: 是否上线\n\n",
                "transcript": [],
            },
        },
        resolved_payload={
            "status": "completed",
            "success": True,
            "response": "pro-response",
            "thread_id": "thread-pro",
        },
    )

    assert result is not None
    assert result["conclusion"] == "judge-response"
    assert [entry["agent"] for entry in result["transcript"]] == ["pro", "con"]
    assert calls == ["con", "judge"]


def test_workflow_collaboration_runtime_aggregates_consensus_responses():
    runtime = WorkflowCollaborationRuntime(
        log_event=lambda workflow, node_id, event, detail="": None,
        agent_callback=lambda prompt: "汇总结论",
        delegate_callback=lambda agent, task, context: {
            "status": "completed",
            "success": True,
            "response": f"{agent}-answer",
            "thread_id": f"thread-{agent}",
            "state_update": {"owner": agent},
        },
    )
    workflow = WorkflowDef(id="wf_consensus", name="consensus")
    node = FlowNode(
        id="consensus_1",
        type=NodeType.CONSENSUS,
        config={"question": "如何发布", "agents": ["planner", "reviewer"]},
    )

    result = runtime.dispatch_node(node, node.config, workflow)

    assert result["consensus"] == "汇总结论"
    assert result["expert_responses"] == {"planner": "planner-answer", "reviewer": "reviewer-answer"}
    assert result["delegation_results"]["planner"]["state_update"] == {"owner": "planner"}
    assert result["delegation_results"]["reviewer"]["thread_id"] == "thread-reviewer"


def test_workflow_collaboration_runtime_consensus_tracks_multiple_pending_expert_approvals():
    runtime = WorkflowCollaborationRuntime(
        log_event=lambda workflow, node_id, event, detail="": None,
        agent_callback=lambda prompt: "unused",
        delegate_callback=lambda agent, task, context: {
            "status": "waiting_approval",
            "success": False,
            "response": f"{agent} paused",
            "approval_id": f"appr_{agent}",
            "thread_id": f"thread-{agent}",
        },
    )
    workflow = WorkflowDef(id="wf_consensus_pending", name="consensus")
    node = FlowNode(
        id="consensus_1",
        type=NodeType.CONSENSUS,
        config={"question": "如何发布", "agents": ["planner", "reviewer"]},
    )

    result = runtime.dispatch_node(node, node.config, workflow)

    assert result["status"] == "waiting_approval"
    assert result["approval_id"] == "appr_planner"
    assert result["approval_ids"] == ["appr_planner", "appr_reviewer"]
    assert [item["agent_name"] for item in result["pending_approvals"]] == ["planner", "reviewer"]


def test_workflow_collaboration_runtime_consensus_resumes_without_rerunning_completed_experts():
    calls = []
    runtime = WorkflowCollaborationRuntime(
        log_event=lambda workflow, node_id, event, detail="": None,
        agent_callback=lambda prompt: "unused",
        delegate_callback=lambda agent, task, context: (
            calls.append(agent)
            or {
                "status": "completed",
                "success": True,
                "response": f"{agent}-summary",
                "thread_id": f"thread-{agent}",
            }
        ),
    )
    workflow = WorkflowDef(id="wf_consensus_resume", name="consensus")
    node = FlowNode(
        id="consensus_1",
        type=NodeType.CONSENSUS,
        config={"question": "如何发布", "agents": ["planner", "reviewer"], "aggregator": "synthesizer"},
    )

    result = runtime.resume_delegated_node(
        node=node,
        workflow=workflow,
        waiting_payload={
            "workflow_pause_mode": "consensus",
            "workflow_pause_state": {
                "phase": "experts",
                "next_agent_index": 1,
                "responses": {"planner": "planner-answer"},
                "delegation_results": {
                    "planner": {"status": "completed", "success": True, "response": "planner-answer"}
                },
            },
        },
        resolved_payload={
            "status": "completed",
            "success": True,
            "response": "reviewer-answer",
            "thread_id": "thread-reviewer",
        },
    )

    assert result is not None
    assert result["expert_responses"] == {"planner": "planner-answer", "reviewer": "reviewer-answer"}
    assert result["consensus"] == "synthesizer-summary"
    assert calls == ["synthesizer"]


def test_workflow_collaboration_runtime_consensus_resumes_specific_pending_expert_without_replaying_others():
    calls = []
    runtime = WorkflowCollaborationRuntime(
        log_event=lambda workflow, node_id, event, detail="": None,
        agent_callback=lambda prompt: "unused",
        delegate_callback=lambda agent, task, context: (
            calls.append(agent)
            or {
                "status": "completed",
                "success": True,
                "response": f"{agent}-summary",
                "thread_id": f"thread-{agent}",
            }
        ),
    )
    workflow = WorkflowDef(id="wf_consensus_specific_resume", name="consensus")
    node = FlowNode(
        id="consensus_1",
        type=NodeType.CONSENSUS,
        config={"question": "如何发布", "agents": ["planner", "reviewer"], "aggregator": "synthesizer"},
    )

    result = runtime.resume_delegated_node(
        node=node,
        workflow=workflow,
        waiting_payload={
            "workflow_pause_mode": "consensus",
            "workflow_pause_state": {
                "phase": "experts",
                "next_agent_index": 2,
                "responses": {"planner": "planner-answer"},
                "delegation_results": {
                    "planner": {"status": "completed", "success": True, "response": "planner-answer"}
                },
                "pending_approvals": [
                    {
                        "agent_name": "reviewer",
                        "task": "如何发布",
                        "approval_id": "appr_reviewer",
                    }
                ],
            },
        },
        resolved_payload={
            "status": "completed",
            "success": True,
            "response": "reviewer-answer",
            "thread_id": "thread-reviewer",
        },
        resolved_approval_id="appr_reviewer",
    )

    assert result is not None
    assert result["expert_responses"] == {"planner": "planner-answer", "reviewer": "reviewer-answer"}
    assert result["consensus"] == "synthesizer-summary"
    assert calls == ["synthesizer"]


def test_workflow_collaboration_runtime_supervisor_falls_back_to_first_worker():
    runtime = WorkflowCollaborationRuntime(
        log_event=lambda workflow, node_id, event, detail="": None,
        agent_callback=lambda prompt: "unknown-worker",
        delegate_callback=lambda agent, task, context: {
            "status": "completed",
            "success": True,
            "response": f"delegated:{agent}",
            "state_update": {"chosen": agent},
        },
    )
    workflow = WorkflowDef(id="wf_supervisor", name="supervisor")
    node = FlowNode(
        id="supervisor_1",
        type=NodeType.SUPERVISOR,
        config={"task": "修复 bug", "workers": ["builder", "reviewer"]},
    )

    result = runtime.dispatch_node(node, node.config, workflow)

    assert result["chosen_worker"] == "builder"
    assert result["response"] == "delegated:builder"
    assert result["delegation"]["state_update"] == {"chosen": "builder"}


def test_workflow_collaboration_runtime_agent_preserves_structured_delegate_feedback():
    runtime = WorkflowCollaborationRuntime(
        log_event=lambda workflow, node_id, event, detail="": None,
        agent_callback=lambda prompt: "unused",
        delegate_callback=lambda agent, task, context: {
            "status": "completed",
            "success": True,
            "response": f"{agent}:{task}",
            "thread_id": "delegate-thread",
            "state_update": {"next_step": "review"},
            "approval_id": None,
        },
    )
    workflow = WorkflowDef(id="wf_agent_structured", name="agent")
    node = FlowNode(
        id="agent_1",
        type=NodeType.AGENT,
        config={"agent_name": "helper", "task": "完成任务"},
    )

    result = runtime.dispatch_node(node, node.config, workflow)

    assert result["response"] == "helper:完成任务"
    assert result["status"] == "completed"
    assert result["thread_id"] == "delegate-thread"
    assert result["state_update"] == {"next_step": "review"}


def test_workflow_collaboration_runtime_agent_surfaces_waiting_approval_payload():
    runtime = WorkflowCollaborationRuntime(
        log_event=lambda workflow, node_id, event, detail="": None,
        agent_callback=lambda prompt: "unused",
        delegate_callback=lambda agent, task, context: {
            "status": "waiting_approval",
            "success": False,
            "response": "paused for approval",
            "approval_id": "appr_123",
            "thread_id": "delegate-thread",
        },
    )
    workflow = WorkflowDef(id="wf_agent_wait", name="agent")
    node = FlowNode(
        id="agent_1",
        type=NodeType.AGENT,
        config={"agent_name": "helper", "task": "完成任务"},
    )

    result = runtime.dispatch_node(node, node.config, workflow)

    assert result["status"] == "waiting_approval"
    assert result["approval_id"] == "appr_123"
    assert result["workflow_pause_kind"] == "delegated_subagent"
    assert result["workflow_pause_mode"] == "agent"


def test_workflow_collaboration_runtime_agent_falls_back_when_delegate_missing():
    events = []
    runtime = WorkflowCollaborationRuntime(
        log_event=lambda workflow, node_id, event, detail="": events.append(event),
        agent_callback=lambda prompt: "root fallback",
        delegate_callback=None,
    )
    workflow = WorkflowDef(id="wf_agent", name="agent")
    node = FlowNode(
        id="agent_1",
        type=NodeType.AGENT,
        config={"agent_name": "helper", "task": "完成任务", "retry_on_fail": True},
    )

    result = runtime.dispatch_node(node, node.config, workflow)

    assert result["fallback"] is True
    assert result["response"] == "root fallback"
    assert "agent_fallback" in events
