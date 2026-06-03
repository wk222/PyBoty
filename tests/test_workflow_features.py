"""Unified tests for Workflow feature modules: execution runtime, collaboration (debate, consensus, task force), Dify-inspired features, temporal and signals, enhancements (variables, database query, file utilities), and error strategies (Eighth Round).

Consolidated and merged from:
* test_workflow_execution_runtime.py
* test_workflow_dify_features.py
* test_workflow_temporal.py
* test_workflow_enhancements.py
* test_workflow_collaboration_runtime.py
"""

from __future__ import annotations

import json
import os
import sqlite3
import tempfile
import time
from datetime import datetime
from typing import Any, Callable
from unittest.mock import MagicMock, patch

import pytest

# Core workflow imports
from core.assets.workflows import (
    run_database_query,
    run_file_read,
    run_file_write,
    run_http_request,
    run_list_operator,
    run_parameter_extractor,
    run_question_classifier,
    run_variable_assigner,
)
from core.assets.workflows.models import (
    WorkflowApprovalPause,
)
from core.assets.workflows.workflow_execution_runtime import (
    WorkflowExecutionRuntime,
)
from core.assets.workflows.workflow_models import (
    BRANCH_NODE_TYPES,
    EdgeState,
    FlowEdge,
    FlowNode,
    NodeExecutionRecord,
    NodeStatus,
    NodeType,
    WorkflowDef,
    WorkflowRunRecord,
    WorkflowStatus,
)
from core.assets.workflows.workflow_collaboration_runtime import (
    WorkflowCollaborationRuntime,
)
from core.assets.workflows.workflow_graph_runtime import (
    WorkflowGraphRuntime,
    _last_completed_output,
)
from core.assets.workflows.workflow_models import OnErrorStrategy
from core.assets.workflows.workflow_node_runtime import (
    WorkflowSignalPause,
    WorkflowTimerPause,
)
from core.assets.workflows.workflow_plugin import (
    dispatch_plugin,
    get_plugin,
    list_plugins,
    register_node_plugin,
    unregister_node_plugin,
)
from core.systems.governance.approval_queue import ApprovalQueue
from core.systems.session import SessionRuntime


# ---------------------------------------------------------------------------
# Helpers for Workflow Execution Runtime
# ---------------------------------------------------------------------------

def _build_runtime(
    *,
    approval_queue: ApprovalQueue,
    workflows: dict[str, WorkflowDef],
    exec_node: Any,
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


def _complete_node(node: FlowNode, output: Any):
    node.status = NodeStatus.COMPLETED
    node.output = output
    return output


# ---------------------------------------------------------------------------
# 1. Workflow Execution Runtime Tests (formerly test_workflow_execution_runtime.py)
# ---------------------------------------------------------------------------

class TestWorkflowExecutionRuntime:
    def test_workflow_execution_runtime_returns_waiting_approval_payload(self):
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

    def test_workflow_execution_runtime_resumes_approved_node_and_finalizes_queue(self):
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

    def test_workflow_execution_runtime_projects_runs_into_session_timeline(self, tmp_path):
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

    def test_workflow_execution_runtime_skips_unselected_condition_branch(self):
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

    def test_workflow_execution_runtime_resumes_waiting_delegated_agent_node(self):
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

    def test_workflow_execution_runtime_requeues_delegated_agent_when_followup_approval_is_needed(self):
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

    def test_workflow_execution_runtime_resumes_waiting_debate_node_via_collaboration_runtime(self):
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

    def test_workflow_execution_runtime_requeues_waiting_consensus_node_with_updated_pause_state(self):
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

    def test_workflow_execution_runtime_resumes_specific_pending_consensus_approval_and_keeps_other_experts_waiting(self):
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

    def test_workflow_execution_runtime_plugin_node_resume_failure_recovery(self):
        queue = ApprovalQueue()
        request = queue.create_request(
            kind="tool_call",
            scope="plugin",
            summary="plugin approval",
            prompt="approve?",
        )
        workflow = WorkflowDef(
            id="wf_plugin_resume",
            name="plugin-resume-demo",
            status=WorkflowStatus.PAUSED,
            resume_token="resume-plugin",
            nodes={
                "plugin1": FlowNode(
                    id="plugin1",
                    type=NodeType.AGENT,
                    status=NodeStatus.WAITING,
                    output={
                        "status": "waiting_approval",
                        "approval_id": request.approval_id,
                        "workflow_pause_kind": "delegated_subagent",
                        "workflow_pause_mode": "debate",
                        "workflow_pause_state": {"step_index": 0},
                    }
                )
            },
        )
        
        def failing_resume(node, workflow, waiting_payload, resolved_payload, resolved_approval_id=""):
            raise ValueError("Simulated plugin crash during resume recovery")

        runtime = _build_runtime(
            approval_queue=queue,
            workflows={"wf_plugin_resume": workflow},
            exec_node=lambda node, workflow, run_id: {"ok": True},
            resume_collaboration_node=failing_resume,
        )

        queue.resolve(request.approval_id, approved=True)

        result = runtime.resume_workflow("wf_plugin_resume", "resume-plugin", True)

        assert result["status"] == "failed"
        assert "Simulated plugin crash" in str(result.get("error", ""))
        assert workflow.status == WorkflowStatus.FAILED
        assert workflow.nodes["plugin1"].status == NodeStatus.FAILED


# ---------------------------------------------------------------------------
# 2. Dify Features Tests (formerly test_workflow_dify_features.py)
# ---------------------------------------------------------------------------

class TestNewNodeTypes:
    def test_node_type_enum_has_new_types(self):
        assert NodeType.HTTP_REQUEST == "http_request"
        assert NodeType.QUESTION_CLASSIFIER == "question_classifier"
        assert NodeType.VARIABLE_ASSIGNER == "variable_assigner"
        assert NodeType.LIST_OPERATOR == "list_operator"
        assert NodeType.PARAMETER_EXTRACTOR == "parameter_extractor"
        assert NodeType.ITERATION == "iteration"

    def test_branch_node_types_includes_question_classifier(self):
        assert NodeType.QUESTION_CLASSIFIER in BRANCH_NODE_TYPES
        assert NodeType.CONDITION in BRANCH_NODE_TYPES
        assert NodeType.ROUTER in BRANCH_NODE_TYPES
        assert NodeType.LLM not in BRANCH_NODE_TYPES


class TestVariableAssigner:
    def test_set_operation(self):
        variables: dict[str, object] = {}
        result = run_variable_assigner(
            {"assignments": [{"variable": "greeting", "value": "hello"}]},
            variables,
            lambda v: v,
        )
        assert result == {"greeting": "hello"}
        assert variables["greeting"] == "hello"

    def test_append_operation(self):
        variables: dict[str, object] = {"items": ["a"]}
        run_variable_assigner(
            {"assignments": [{"variable": "items", "value": "b", "operation": "append"}]},
            variables,
            lambda v: v,
        )
        assert variables["items"] == ["a", "b"]

    def test_increment_operation(self):
        variables: dict[str, object] = {"count": 5}
        run_variable_assigner(
            {"assignments": [{"variable": "count", "value": 3, "operation": "increment"}]},
            variables,
            lambda v: v,
        )
        assert variables["count"] == 8.0

    def test_shorthand_config(self):
        variables: dict[str, object] = {}
        run_variable_assigner({"variable": "x", "value": 42}, variables, lambda v: v)
        assert variables["x"] == 42


class TestListOperator:
    def test_sort(self):
        result = run_list_operator({"operation": "sort", "data": [3, 1, 2]}, lambda v: v)
        assert result == [1, 2, 3]

    def test_sort_by_key(self):
        data = [{"name": "b"}, {"name": "a"}]
        result = run_list_operator({"operation": "sort", "data": data, "key": "name"}, lambda v: v)
        assert result[0]["name"] == "a"

    def test_reverse(self):
        result = run_list_operator({"operation": "reverse", "data": [1, 2, 3]}, lambda v: v)
        assert result == [3, 2, 1]

    def test_unique(self):
        result = run_list_operator({"operation": "unique", "data": [1, 2, 2, 3, 3]}, lambda v: v)
        assert result == [1, 2, 3]

    def test_flatten(self):
        result = run_list_operator({"operation": "flatten", "data": [[1, 2], [3], 4]}, lambda v: v)
        assert result == [1, 2, 3, 4]

    def test_slice(self):
        result = run_list_operator({"operation": "slice", "data": [1, 2, 3, 4], "start": 1, "end": 3}, lambda v: v)
        assert result == [2, 3]

    def test_head_tail(self):
        data = [1, 2, 3, 4, 5]
        assert run_list_operator({"operation": "head", "data": data, "count": 2}, lambda v: v) == [1, 2]
        assert run_list_operator({"operation": "tail", "data": data, "count": 2}, lambda v: v) == [4, 5]

    def test_length(self):
        assert run_list_operator({"operation": "length", "data": [1, 2, 3]}, lambda v: v) == 3

    def test_contains(self):
        assert run_list_operator({"operation": "contains", "data": [1, 2, 3], "value": 2}, lambda v: v) is True
        assert run_list_operator({"operation": "contains", "data": [1, 2, 3], "value": 9}, lambda v: v) is False

    def test_join(self):
        result = run_list_operator({"operation": "join", "data": ["a", "b", "c"], "separator": "-"}, lambda v: v)
        assert result == "a-b-c"

    def test_group_by(self):
        data = [{"type": "a", "v": 1}, {"type": "b", "v": 2}, {"type": "a", "v": 3}]
        result = run_list_operator({"operation": "group_by", "data": data, "key": "type"}, lambda v: v)
        assert len(result["a"]) == 2
        assert len(result["b"]) == 1

    def test_zip(self):
        result = run_list_operator({"operation": "zip", "data": [1, 2], "other": ["a", "b"]}, lambda v: v)
        assert result == [[1, "a"], [2, "b"]]


class TestQuestionClassifier:
    def test_classification(self):
        classes = [
            {"id": "billing", "name": "Billing", "description": "Payment questions"},
            {"id": "tech", "name": "Technical", "description": "Technical support"},
        ]
        mock_callback = MagicMock(return_value="billing")
        result = run_question_classifier({"query": "How do I pay?", "classes": classes}, mock_callback)
        assert result["class_id"] == "billing"
        assert result["_branch"] == "billing"
        mock_callback.assert_called_once()

    def test_fallback_to_first_class(self):
        classes = [{"id": "a"}, {"id": "b"}]
        mock_callback = MagicMock(return_value="invalid_class")
        result = run_question_classifier({"query": "test", "classes": classes}, mock_callback)
        assert result["class_id"] == "a"

    def test_no_classes_raises(self):
        with pytest.raises(ValueError, match="at least one class"):
            run_question_classifier({"query": "test", "classes": []}, MagicMock())


class TestParameterExtractor:
    def test_extraction(self):
        params = [
            {"name": "name", "type": "string", "required": True, "description": "Person name"},
            {"name": "age", "type": "integer", "required": False, "description": "Person age"},
        ]
        mock_callback = MagicMock(return_value='{"name": "Alice", "age": 30}')
        result = run_parameter_extractor({"text": "Alice is 30 years old", "parameters": params}, mock_callback)
        assert result["name"] == "Alice"
        assert result["age"] == 30


class TestHttpRequest:
    @patch("core.assets.workflows.workflow_nodes_extended.urlopen")
    def test_get_json(self, mock_urlopen):
        mock_response = MagicMock()
        mock_response.read.return_value = b'{"key": "value"}'
        mock_response.status = 200
        mock_response.getheaders.return_value = [("Content-Type", "application/json")]
        mock_response.__enter__ = MagicMock(return_value=mock_response)
        mock_response.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = mock_response

        result = run_http_request({"url": "https://example.com/api", "method": "GET"})
        assert result["status_code"] == 200
        assert result["body"] == {"key": "value"}
        assert result["success"] is True

    def test_missing_url_raises(self):
        with pytest.raises(ValueError, match="url"):
            run_http_request({"method": "GET"})


class TestEdgeState:
    def test_edge_state_enum(self):
        assert EdgeState.UNKNOWN == "unknown"
        assert EdgeState.TAKEN == "taken"
        assert EdgeState.SKIPPED == "skipped"

    def test_flow_edge_has_state(self):
        edge = FlowEdge(id="e1", source="a", target="b")
        assert edge.state == EdgeState.UNKNOWN
        assert edge.source_handle == "source"

    def test_edge_to_dict_includes_state(self):
        edge = FlowEdge(id="e1", source="a", target="b", state=EdgeState.TAKEN)
        d = edge.to_dict()
        assert d["state"] == "taken"
        assert d["source_handle"] == "source"


class TestGraphRuntimeEdgeProcessing:
    def _make_workflow(self):
        wf = WorkflowDef(id="test", name="test")
        wf.nodes["start"] = FlowNode(id="start", type=NodeType.START, status=NodeStatus.COMPLETED)
        wf.nodes["cond"] = FlowNode(id="cond", type=NodeType.CONDITION)
        wf.nodes["branch_a"] = FlowNode(id="branch_a", type=NodeType.EXEC)
        wf.nodes["branch_b"] = FlowNode(id="branch_b", type=NodeType.EXEC)

        wf.edges = [
            FlowEdge(id="e1", source="start", target="cond"),
            FlowEdge(id="e2", source="cond", target="branch_a", source_handle="true"),
            FlowEdge(id="e3", source="cond", target="branch_b", source_handle="false"),
        ]
        return wf

    def test_process_branch_node_marks_edges(self):
        wf = self._make_workflow()
        rt = WorkflowGraphRuntime()
        ready = rt.process_node_success(wf, "cond", selected_target="branch_a")
        assert wf.edges[1].state == EdgeState.TAKEN
        assert wf.edges[2].state == EdgeState.SKIPPED
        assert "branch_a" in ready

    def test_skip_propagation(self):
        wf = self._make_workflow()
        rt = WorkflowGraphRuntime()
        rt.process_node_success(wf, "cond", selected_target="branch_a")
        assert wf.nodes["branch_b"].status == NodeStatus.SKIPPED

    def test_non_branch_marks_all_taken(self):
        wf = WorkflowDef(id="test", name="test")
        wf.nodes["a"] = FlowNode(id="a", type=NodeType.EXEC, status=NodeStatus.COMPLETED)
        wf.nodes["b"] = FlowNode(id="b", type=NodeType.EXEC)
        wf.nodes["c"] = FlowNode(id="c", type=NodeType.EXEC)
        wf.edges = [
            FlowEdge(id="e1", source="a", target="b"),
            FlowEdge(id="e2", source="a", target="c"),
        ]
        rt = WorkflowGraphRuntime()
        ready = rt.process_node_success(wf, "a")
        assert wf.edges[0].state == EdgeState.TAKEN
        assert wf.edges[1].state == EdgeState.TAKEN
        assert set(ready) == {"b", "c"}

    def test_reset_edge_states(self):
        wf = WorkflowDef(id="test", name="test")
        wf.edges = [FlowEdge(id="e1", source="a", target="b", state=EdgeState.TAKEN)]
        rt = WorkflowGraphRuntime()
        rt.reset_edge_states(wf)
        assert wf.edges[0].state == EdgeState.UNKNOWN


class TestDifyVariableTemplates:
    def test_dify_template_resolution(self):
        rt = WorkflowGraphRuntime()
        wf = WorkflowDef(id="test", name="test")
        wf.variables["step1.output"] = "hello world"
        result = rt.resolve_var("Result: {{#step1.output#}}", wf)
        assert result == "Result: hello world"

    def test_dify_template_with_nested_access(self):
        rt = WorkflowGraphRuntime()
        wf = WorkflowDef(id="test", name="test")
        wf.variables["api.output"] = {"data": {"name": "test"}}
        result = rt.resolve_var("{{#api.output.data.name#}}", wf)
        assert result == "test"

    def test_dollar_template_still_works(self):
        rt = WorkflowGraphRuntime()
        wf = WorkflowDef(id="test", name="test")
        wf.variables["name"] = "PyBot"
        assert rt.resolve_var("Hello ${name}", wf) == "Hello PyBot"

    def test_mixed_templates(self):
        rt = WorkflowGraphRuntime()
        wf = WorkflowDef(id="test", name="test")
        wf.variables["step1.output"] = "dify"
        wf.variables["version"] = "5.0"
        result = rt.resolve_var("{{#step1.output#}} v${version}", wf)
        assert result == "dify v5.0"

    def test_deep_get_with_dotted_key(self):
        rt = WorkflowGraphRuntime()
        variables = {"a": {"b": {"c": 42}}}
        assert rt._deep_get(variables, "a.b.c") == 42

    def test_deep_get_flat_key(self):
        rt = WorkflowGraphRuntime()
        variables = {"a.b.c": "flat"}
        assert rt._deep_get(variables, "a.b.c") == "flat"


class TestExecutionRecords:
    def test_node_execution_record_to_dict(self):
        rec = NodeExecutionRecord(
            node_id="step1",
            node_type="llm",
            status="completed",
            inputs={"prompt": "hello"},
            outputs={"result": "world"},
            elapsed_time=1.234,
        )
        d = rec.to_dict()
        assert d["node_id"] == "step1"
        assert d["elapsed_time"] == 1.234
        assert d["outputs"]["result"] == "world"

    def test_workflow_run_record_to_dict(self):
        run = WorkflowRunRecord(
            run_id="abc123",
            workflow_id="wf1",
            workflow_name="Test Workflow",
            status="completed",
            total_nodes=3,
            completed_nodes=3,
            elapsed_time=2.5,
        )
        d = run.to_dict()
        assert d["run_id"] == "abc123"
        assert d["total_nodes"] == 3
        assert d["elapsed_time"] == 2.5


# ---------------------------------------------------------------------------
# 3. Temporal & Signals Tests (formerly test_workflow_temporal.py)
# ---------------------------------------------------------------------------

class TestPauseExceptions:
    def test_timer_pause_attrs(self):
        exc = WorkflowTimerPause("wf1", "n1", 9999.0, "tok123")
        assert exc.workflow_id == "wf1"
        assert exc.node_id == "n1"
        assert exc.resume_at == 9999.0
        assert exc.resume_token == "tok123"

    def test_signal_pause_attrs(self):
        exc = WorkflowSignalPause("wf2", "n2", "order_completed", "tok456")
        assert exc.workflow_id == "wf2"
        assert exc.signal_name == "order_completed"
        assert exc.resume_token == "tok456"


class TestIdempotencyKey:
    def test_idempotency_key_on_node(self):
        node = FlowNode(id="n1", type=NodeType.EXEC, idempotency_key="unique_123")
        assert node.idempotency_key == "unique_123"

    def test_idempotency_default_none(self):
        node = FlowNode(id="n2", type=NodeType.EXEC)
        assert node.idempotency_key is None


class TestWaitSignalNodeType:
    def test_enum_exists(self):
        assert NodeType.WAIT_SIGNAL.value == "wait_signal"


class TestDurableDelay:
    def _make_runtime(self):
        from core.assets.workflows.workflow_node_runtime import WorkflowNodeRuntime

        return WorkflowNodeRuntime(
            workspace_dir="/tmp/test",
            approval_queue=MagicMock(),
            save_workflow=MagicMock(),
            load_workflow=MagicMock(),
            resume_workflow=MagicMock(),
            run_workflow=MagicMock(),
            resolve_var=lambda v, w: v,
            resolve_config=lambda c, w: c,
            evaluate_condition=lambda c, w: True,
            get_predecessors=lambda w, n: [],
            workflow_approval_fingerprint=lambda **kw: "fp",
            log_event=lambda *a: None,
            extra_dispatch=lambda *a: None,
        )

    def test_short_delay_runs_inline(self):
        runtime = self._make_runtime()
        node = FlowNode(id="d1", type=NodeType.DELAY, config={"seconds": 0.01})
        wf = WorkflowDef(id="w1", name="test")
        result = runtime.exec_node(node, wf)
        assert result["durable"] is False
        assert node.status == NodeStatus.COMPLETED

    def test_long_delay_raises_timer_pause(self):
        runtime = self._make_runtime()
        node = FlowNode(id="d2", type=NodeType.DELAY, config={"seconds": 3600, "durable": True})
        wf = WorkflowDef(id="w2", name="test")
        with pytest.raises(WorkflowTimerPause) as exc_info:
            runtime.exec_node(node, wf)
        assert exc_info.value.resume_at > time.time()

    def test_explicit_durable_flag(self):
        runtime = self._make_runtime()
        node = FlowNode(id="d3", type=NodeType.DELAY, config={"seconds": 10, "durable": True})
        wf = WorkflowDef(id="w3", name="test")
        with pytest.raises(WorkflowTimerPause):
            runtime.exec_node(node, wf)


class TestWaitSignal:
    def _make_runtime(self):
        from core.assets.workflows.workflow_node_runtime import WorkflowNodeRuntime

        return WorkflowNodeRuntime(
            workspace_dir="/tmp/test",
            approval_queue=MagicMock(),
            save_workflow=MagicMock(),
            load_workflow=MagicMock(),
            resume_workflow=MagicMock(),
            run_workflow=MagicMock(),
            resolve_var=lambda v, w: v,
            resolve_config=lambda c, w: c,
            evaluate_condition=lambda c, w: True,
            get_predecessors=lambda w, n: [],
            workflow_approval_fingerprint=lambda **kw: "fp",
            log_event=lambda *a: None,
            extra_dispatch=lambda *a: None,
        )

    def test_wait_signal_raises_pause(self):
        runtime = self._make_runtime()
        node = FlowNode(id="ws1", type=NodeType.WAIT_SIGNAL, config={"signal_name": "payment_done"})
        wf = WorkflowDef(id="w4", name="test")
        with pytest.raises(WorkflowSignalPause) as exc_info:
            runtime.exec_node(node, wf)
        assert exc_info.value.signal_name == "payment_done"

    def test_wait_signal_missing_name(self):
        runtime = self._make_runtime()
        node = FlowNode(id="ws2", type=NodeType.WAIT_SIGNAL, config={})
        wf = WorkflowDef(id="w5", name="test")
        with pytest.raises(Exception, match="signal_name"):
            runtime.exec_node(node, wf)


# ---------------------------------------------------------------------------
# 4. Enhancements Tests (formerly test_workflow_enhancements.py)
# ---------------------------------------------------------------------------

class TestBuiltinVariables:
    def _make_workflow(self) -> WorkflowDef:
        wf = WorkflowDef(id="test", name="test")
        wf.variables["input"] = {"query": "hello"}
        return wf

    def _graph(self) -> WorkflowGraphRuntime:
        return WorkflowGraphRuntime()

    def test_input_variable(self):
        wf = self._make_workflow()
        result = self._graph().resolve_var("${input}", wf)
        assert result == {"query": "hello"}

    def test_input_in_template(self):
        wf = self._make_workflow()
        result = self._graph().resolve_var("Query is: ${input}", wf)
        assert "hello" in result

    def test_last_variable_empty(self):
        wf = self._make_workflow()
        result = self._graph().resolve_var("${last}", wf)
        assert result is None

    def test_last_variable_with_completed_nodes(self):
        wf = self._make_workflow()
        n1 = FlowNode(id="n1", type=NodeType.EXEC, status=NodeStatus.COMPLETED, output="first", completed_at=1.0)
        n2 = FlowNode(id="n2", type=NodeType.EXEC, status=NodeStatus.COMPLETED, output="second", completed_at=2.0)
        wf.nodes["n1"] = n1
        wf.nodes["n2"] = n2
        result = self._graph().resolve_var("${last}", wf)
        assert result == "second"

    def test_env_variable(self):
        wf = self._make_workflow()
        with patch.dict(os.environ, {"TEST_WF_VAR": "env_value_123"}):
            result = self._graph().resolve_var("${env.TEST_WF_VAR}", wf)
        assert result == "env_value_123"

    def test_env_variable_missing(self):
        wf = self._make_workflow()
        result = self._graph().resolve_var("${env.NONEXISTENT_VAR_XYZ}", wf)
        assert result == ""


class TestLastCompletedOutput:
    def test_empty_workflow(self):
        wf = WorkflowDef(id="t", name="t")
        assert _last_completed_output(wf) is None

    def test_picks_latest(self):
        wf = WorkflowDef(id="t", name="t")
        wf.nodes["a"] = FlowNode(id="a", type=NodeType.EXEC, status=NodeStatus.COMPLETED, output="A", completed_at=10.0)
        wf.nodes["b"] = FlowNode(id="b", type=NodeType.EXEC, status=NodeStatus.COMPLETED, output="B", completed_at=20.0)
        wf.nodes["c"] = FlowNode(id="c", type=NodeType.EXEC, status=NodeStatus.PENDING)
        assert _last_completed_output(wf) == "B"


class TestDatabaseQueryNode:
    def test_sqlite_select(self):
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name
        try:
            conn = sqlite3.connect(db_path)
            conn.execute("CREATE TABLE items (id INTEGER, name TEXT)")
            conn.execute("INSERT INTO items VALUES (1, 'alpha')")
            conn.execute("INSERT INTO items VALUES (2, 'beta')")
            conn.commit()
            conn.close()

            result = run_database_query({
                "provider": "sqlite",
                "connection_string": db_path,
                "query": "SELECT * FROM items ORDER BY id",
            })
            assert result["row_count"] == 2
            assert result["rows"][0]["name"] == "alpha"
        finally:
            os.unlink(db_path)

    def test_readonly_blocks_insert(self):
        with pytest.raises(ValueError, match="readonly mode blocks INSERT"):
            run_database_query({
                "provider": "sqlite",
                "query": "INSERT INTO t VALUES (1)",
                "readonly": True,
            })

    def test_readonly_allows_select(self):
        result = run_database_query({
            "provider": "sqlite",
            "connection_string": ":memory:",
            "query": "SELECT 1 AS v",
            "readonly": True,
        })
        assert result["rows"][0]["v"] == 1

    def test_empty_query_raises(self):
        with pytest.raises(ValueError, match="non-empty"):
            run_database_query({"query": ""})


class TestFileNodes:
    def test_file_write_and_read(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            run_file_write({"path": "test.txt", "content": "hello world"}, tmpdir)
            result = run_file_read({"path": "test.txt"}, tmpdir)
            assert result["content"] == "hello world"
            assert result["size"] > 0

    def test_file_append(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            run_file_write({"path": "log.txt", "content": "line1\n"}, tmpdir)
            run_file_write({"path": "log.txt", "content": "line2\n", "mode": "append"}, tmpdir)
            result = run_file_read({"path": "log.txt"}, tmpdir)
            assert "line1" in result["content"]
            assert "line2" in result["content"]

    def test_file_read_missing(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            with pytest.raises(FileNotFoundError):
                run_file_read({"path": "nope.txt"}, tmpdir)

    def test_path_traversal_blocked(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            with pytest.raises(ValueError, match="路径越界"):
                run_file_read({"path": "../../etc/passwd"}, tmpdir)

    def test_write_creates_subdirectory(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            run_file_write({"path": "sub/dir/file.txt", "content": "nested"}, tmpdir)
            result = run_file_read({"path": "sub/dir/file.txt"}, tmpdir)
            assert result["content"] == "nested"

    def test_empty_path_raises(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            with pytest.raises(ValueError, match="'path'"):
                run_file_read({"path": ""}, tmpdir)


class _DummyPlugin:
    node_type = "custom_echo"

    def execute(self, config: dict[str, Any], context: dict[str, Any]) -> Any:
        return {"echo": config.get("message", ""), "vars_count": len(context.get("variables", {}))}

    def schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {"message": {"type": "string"}},
        }


class TestPluginRegistry:
    def setup_method(self):
        unregister_node_plugin("custom_echo")

    def teardown_method(self):
        unregister_node_plugin("custom_echo")

    def test_register_and_get(self):
        plugin = _DummyPlugin()
        register_node_plugin(plugin)
        assert get_plugin("custom_echo") is plugin

    def test_list_plugins(self):
        register_node_plugin(_DummyPlugin())
        plugins = list_plugins()
        assert "custom_echo" in plugins

    def test_dispatch_plugin(self):
        register_node_plugin(_DummyPlugin())
        result = dispatch_plugin("custom_echo", {"message": "hi"}, {"variables": {"a": 1}})
        assert result["echo"] == "hi"
        assert result["vars_count"] == 1

    def test_dispatch_missing_raises(self):
        with pytest.raises(KeyError, match="custom_echo"):
            dispatch_plugin("custom_echo", {}, {})

    def test_unregister(self):
        register_node_plugin(_DummyPlugin())
        assert unregister_node_plugin("custom_echo") is True
        assert get_plugin("custom_echo") is None

    def test_unregister_missing(self):
        assert unregister_node_plugin("nonexistent") is False


# ---------------------------------------------------------------------------
# 5. Workflow Collaboration Runtime Tests (formerly test_workflow_collaboration_runtime.py)
# ---------------------------------------------------------------------------

class TestWorkflowCollaborationRuntime:
    def test_workflow_collaboration_runtime_runs_debate_and_uses_root_judge(self):
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

    def test_workflow_collaboration_runtime_debate_resumes_without_rerunning_completed_turn(self):
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

    def test_workflow_collaboration_runtime_aggregates_consensus_responses(self):
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

    def test_workflow_collaboration_runtime_consensus_tracks_multiple_pending_expert_approvals(self):
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

    def test_workflow_collaboration_runtime_consensus_resumes_without_rerunning_completed_experts(self):
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

    def test_workflow_collaboration_runtime_consensus_resumes_specific_pending_expert_without_replaying_others(self):
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

    def test_workflow_collaboration_runtime_supervisor_falls_back_to_first_worker(self):
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

    def test_workflow_collaboration_runtime_agent_preserves_structured_delegate_feedback(self):
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

    def test_workflow_collaboration_runtime_agent_surfaces_waiting_approval_payload(self):
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

    def test_workflow_collaboration_runtime_agent_falls_back_when_delegate_missing(self):
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
