"""Unified tests for core Workflow components: specifications, definitions, graph, node runtime, registry, storage, and engine factory (Eighth Round).

Consolidated and merged from:
* test_workflow_spec.py
* test_workflow_definition_runtime.py
* test_workflow_persistence.py
* test_workflow_engine_factory.py
* test_workflow_node_runtime.py
* test_workflow_graph_runtime.py
* test_workflow_registry_runtime.py
* test_workflow_lifecycle_runtime.py
"""

from __future__ import annotations

import json
import os
import tempfile
import time
from datetime import datetime
from pathlib import Path

import pytest

# Core imports
from core.assets.workflows.execution import WorkflowDefinitionRuntime
from core.assets.workflows.models import NodeType as ModelNodeType
from core.assets.workflows.pyflow_engine import PyFlowEngine
from core.assets.workflows.task_scheduler import (
    ScheduledTask,
    _cron_field_matches,
    cron_matches,
)
from core.assets.workflows.workflow_engine_factory import (
    build_workflow_engine_runtime_bundle,
)
from core.assets.workflows.workflow_graph_runtime import WorkflowGraphRuntime
from core.assets.workflows.workflow_lifecycle_runtime import WorkflowLifecycleRuntime
from core.assets.workflows.workflow_models import (
    FlowEdge,
    FlowNode,
    NodeStatus,
    NodeType,
    WorkflowDef,
    WorkflowStatus,
)
from core.assets.workflows.workflow_node_runtime import (
    WorkflowApprovalPause,
    WorkflowNodeRuntime,
)
from core.assets.workflows.workflow_registry_runtime import WorkflowRegistryRuntime
from core.assets.workflows.workflow_spec import (
    export_workflow_spec,
    parse_workflow_spec,
    strip_workflow_runtime,
)
from core.assets.workflows.workflow_storage import WorkflowStorage
from core.systems.governance.approval_queue import ApprovalQueue

SPEC_TEXT = """
name: demo_workflow
description: Demo workflow

nodes:
  - id: step1
    type: exec
    command: echo hello
  - id: step2
    type: llm
    prompt: "summarize ${step1.output}"

edges:
  - step1 -> step2
""".strip()


# ---------------------------------------------------------------------------
# 1. Workflow Spec Tests (formerly test_workflow_spec.py)
# ---------------------------------------------------------------------------

class TestWorkflowSpec:
    def test_workflow_spec_round_trip(self):
        definition = parse_workflow_spec(SPEC_TEXT)

        assert definition["name"] == "demo_workflow"
        assert definition["nodes"][0]["type"] == "start"
        assert definition["nodes"][-1]["type"] == "end"

        rendered = export_workflow_spec(definition)
        reparsed = parse_workflow_spec(rendered)

        assert reparsed["name"] == "demo_workflow"
        assert [node["id"] for node in reparsed["nodes"][1:-1]] == ["step1", "step2"]

    def test_strip_workflow_runtime_removes_runtime_fields(self):
        definition = parse_workflow_spec(SPEC_TEXT)
        definition["status"] = "running"
        definition["resume_token"] = "token"
        definition["nodes"][1]["status"] = "completed"
        definition["nodes"][1]["output"] = {"value": 1}

        stripped = strip_workflow_runtime(definition)

        assert "status" not in stripped
        assert "resume_token" not in stripped
        assert "status" not in stripped["nodes"][1]
        assert "output" not in stripped["nodes"][1]

    def test_workflow_spec_api_accepts_spec(self, client):
        create_response = client.post(
            "/api/workflows/from-spec",
            json={"name": "demo_spec", "spec_content": SPEC_TEXT},
        )
        assert create_response.status_code == 200
        assert create_response.json()["success"] is True

        definition_response = client.get("/api/workflows/demo_spec/definition")
        assert definition_response.status_code == 200

        payload = definition_response.json()
        assert payload["spec_content"]
        assert "yaml_content" not in payload


# ---------------------------------------------------------------------------
# 2. Workflow Definition Runtime Tests (formerly test_workflow_definition_runtime.py)
# ---------------------------------------------------------------------------

class TestWorkflowDefinitionRuntime:
    def test_workflow_definition_runtime_builds_linear_workflow_without_mutating_input(self):
        runtime = WorkflowDefinitionRuntime()
        payload = {
            "name": "demo",
            "nodes": [
                {
                    "id": "lookup",
                    "type": "tool",
                    "label": "Lookup",
                    "tool": "search_docs",
                }
            ],
        }

        workflow = runtime.build_workflow(payload)

        assert [node["id"] for node in payload["nodes"]] == ["lookup"]
        assert list(workflow.nodes) == ["_start", "lookup", "_end"]
        assert workflow.nodes["lookup"].type == ModelNodeType.TOOL
        assert workflow.nodes["lookup"].config == {"tool": "search_docs"}
        assert [(edge.source, edge.target) for edge in workflow.edges] == [
            ("_start", "lookup"),
            ("lookup", "_end"),
        ]

    def test_workflow_definition_runtime_parses_json_definition(self):
        runtime = WorkflowDefinitionRuntime()
        definition = json.dumps(
            {
                "name": "demo",
                "tags": "ops, nightly",
                "nodes": [
                    {
                        "id": "code_step",
                        "type": "code",
                        "code": "print('hi')",
                        "position": "{'x': 20, 'y': 40}",
                    }
                ],
            },
            ensure_ascii=False,
        )

        workflow = runtime.parse_workflow(definition)

        assert workflow.tags == ["ops", "nightly"]
        assert workflow.nodes["code_step"].config["code"] == "print('hi')"
        assert workflow.nodes["code_step"].position == {"x": 20, "y": 40}

    def test_workflow_definition_runtime_preserves_declared_edges(self):
        runtime = WorkflowDefinitionRuntime()
        workflow = runtime.build_workflow(
            {
                "name": "branched",
                "nodes": [
                    {"id": "_start", "type": "start"},
                    {"id": "route", "type": "condition"},
                    {"id": "yes", "type": "exec"},
                    {"id": "_end", "type": "end"},
                ],
                "edges": [
                    {"source": "_start", "target": "route"},
                    {"source": "route", "target": "yes", "condition": "ok"},
                    {"source": "yes", "target": "_end"},
                ],
            }
        )

        assert [(edge.source, edge.target, edge.condition) for edge in workflow.edges] == [
            ("_start", "route", None),
            ("route", "yes", "ok"),
            ("yes", "_end", None),
        ]


# ---------------------------------------------------------------------------
# 3. Cron & Scheduling Tests (formerly test_workflow_persistence.py)
# ---------------------------------------------------------------------------

class TestCronMatches:
    def test_every_minute(self):
        dt = datetime(2026, 3, 21, 14, 30)
        assert cron_matches("* * * * *", dt) is True

    def test_specific_minute_hour(self):
        dt = datetime(2026, 3, 21, 9, 15)
        assert cron_matches("15 9 * * *", dt) is True
        assert cron_matches("30 9 * * *", dt) is False

    def test_every_5_minutes(self):
        dt = datetime(2026, 3, 21, 10, 0)
        assert cron_matches("*/5 * * * *", dt) is True
        dt2 = datetime(2026, 3, 21, 10, 3)
        assert cron_matches("*/5 * * * *", dt2) is False

    def test_range(self):
        dt = datetime(2026, 3, 21, 10, 15)
        assert cron_matches("10-20 * * * *", dt) is True
        assert cron_matches("0-5 * * * *", dt) is False

    def test_list(self):
        dt = datetime(2026, 3, 21, 10, 15)
        assert cron_matches("0,15,30,45 * * * *", dt) is True
        assert cron_matches("0,10,20,40 * * * *", dt) is False

    def test_day_of_month(self):
        dt = datetime(2026, 3, 21, 10, 0)
        assert cron_matches("0 10 21 * *", dt) is True
        assert cron_matches("0 10 22 * *", dt) is False

    def test_month(self):
        dt = datetime(2026, 3, 21, 10, 0)
        assert cron_matches("0 10 * 3 *", dt) is True
        assert cron_matches("0 10 * 4 *", dt) is False

    def test_day_of_week(self):
        dt = datetime(2026, 3, 21, 10, 0)  # Saturday (5 in 0=Mon)
        dow = dt.weekday()  # 5
        assert cron_matches(f"0 10 * * {dow}", dt) is True
        assert cron_matches(f"0 10 * * {(dow + 1) % 7}", dt) is False

    def test_invalid_format(self):
        dt = datetime(2026, 3, 21, 10, 0)
        assert cron_matches("invalid", dt) is False
        assert cron_matches("* * *", dt) is False

    def test_step_with_range(self):
        dt = datetime(2026, 3, 21, 10, 6)
        assert cron_matches("2-10/2 * * * *", dt) is True
        dt2 = datetime(2026, 3, 21, 10, 7)
        assert cron_matches("2-10/2 * * * *", dt2) is False


class TestCronFieldMatches:
    def test_star(self):
        assert _cron_field_matches("*", 5) is True

    def test_exact(self):
        assert _cron_field_matches("10", 10) is True
        assert _cron_field_matches("10", 11) is False

    def test_step(self):
        assert _cron_field_matches("*/10", 20) is True
        assert _cron_field_matches("*/10", 25) is False


class TestScheduledTaskRunOnce:
    def test_run_once_at_in_dict(self):
        task = ScheduledTask(
            name="once", description="d", cron="* * * * *",
            prompt="p", run_once_at=1234567890.0,
        )
        d = task.to_dict()
        assert d["run_once_at"] == 1234567890.0

    def test_run_once_at_not_in_dict_when_none(self):
        task = ScheduledTask(name="x", description="d", cron="*", prompt="p")
        d = task.to_dict()
        assert "run_once_at" not in d


class TestPyFlowEnginePauseCancel:
    def _make_engine(self, tmpdir: str):
        return PyFlowEngine(workspace_dir=tmpdir)

    def test_pause_nonexistent(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            engine = self._make_engine(tmpdir)
            result = engine.pause_workflow("no_such_id")
            assert result["success"] is False


# ---------------------------------------------------------------------------
# 4. Workflow Engine Factory Tests (formerly test_workflow_engine_factory.py)
# ---------------------------------------------------------------------------

class TestWorkflowEngineFactory:
    def test_workflow_engine_factory_builds_shared_runtime_bundle(self, tmp_path):
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


# ---------------------------------------------------------------------------
# 5. Workflow Node Runtime Tests (formerly test_workflow_node_runtime.py)
# ---------------------------------------------------------------------------

class TestWorkflowNodeRuntime:
    def test_workflow_node_runtime_executes_tool_nodes_and_updates_state(self):
        runtime = self._build_runtime(tool_callback=lambda tool_name, args: {"tool": tool_name, "args": args})
        workflow = WorkflowDef(id="wf_tool", name="tool-demo")
        node = FlowNode(id="call_tool", type=NodeType.TOOL, config={"tool": "lookup", "args": {"q": "hi"}})

        result = runtime.exec_node(node, workflow)

        assert result == {"tool": "lookup", "args": {"q": "hi"}}
        assert node.status.value == "completed"
        assert workflow.variables["call_tool.status"] == "completed"
        assert workflow.variables["call_tool.output"] == result

    def test_workflow_node_runtime_approve_node_creates_pause_request(self):
        saved = []
        queue = ApprovalQueue()
        runtime = self._build_runtime(
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

    def test_workflow_node_runtime_delegated_pause_attaches_shared_resume_metadata(self):
        saved = []
        queue = ApprovalQueue()
        request = queue.create_request(
            kind="tool_call",
            scope="subagent:helper",
            summary="helper approval",
            prompt="allow helper?",
        )
        runtime = self._build_runtime(
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
        self,
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


# ---------------------------------------------------------------------------
# 6. Workflow Graph Runtime Tests (formerly test_workflow_graph_runtime.py)
# ---------------------------------------------------------------------------

class TestWorkflowGraphRuntime:
    def test_workflow_graph_runtime_resolves_config_and_ready_nodes(self):
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

    def test_workflow_graph_runtime_detects_cycles(self):
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


# ---------------------------------------------------------------------------
# 7. Workflow Registry Runtime Tests (formerly test_workflow_registry_runtime.py)
# ---------------------------------------------------------------------------

class TestWorkflowRegistryRuntime:
    def test_workflow_registry_runtime_lists_active_workflows_and_graphs(self):
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

    def test_workflow_registry_runtime_returns_none_for_unknown_workflow(self):
        runtime = WorkflowRegistryRuntime()
        assert runtime.get_workflow_graph("missing") is None


# ---------------------------------------------------------------------------
# 8. Workflow Lifecycle Runtime Tests (formerly test_workflow_lifecycle_runtime.py)
# ---------------------------------------------------------------------------

class TestWorkflowLifecycleRuntime:
    def test_workflow_lifecycle_runtime_manages_definitions_and_runtime_snapshots(self, tmp_path):
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

    def test_workflow_lifecycle_runtime_surfaces_registry_and_pending_approvals(self, tmp_path):
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
