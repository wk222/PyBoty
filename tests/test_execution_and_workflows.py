from __future__ import annotations

import json
import threading
import time
from unittest.mock import MagicMock

import pytest
from pydantic import BaseModel, Field

from core.systems.execution.execution_analysis import analyze_python_error
from core.systems.execution.execution_loop import ExecCodeTool, ScanProjectTool
from core.systems.execution.execution_workspace import resolve_workspace_path
from core.systems.execution.execution_runtime import ExecutionRuntime
from core.systems.execution.execution_scanner import ProjectScanner
from core.systems.execution.execution_validation import IterativeResourceValidator
from core.systems.runtime.task_runtime import TaskRuntimeService
from core.systems.governance.approval_queue import ApprovalQueue
from core.systems.governance.execution_protocol import (
    ApprovalResolutionContext,
    WaitingApprovalPayload,
    WorkflowPauseMetadata,
    attach_workflow_pause_metadata,
    normalize_pending_approval_refs,
)
from core.assets.workflows.workflow_execution_runtime import WorkflowExecutionRuntime
from core.assets.workflows.workflow_models import (
    FlowEdge,
    FlowNode,
    NodeStatus,
    NodeType,
    WorkflowDef,
    NodeExceptionConfig,
)
from core.assets.workflows.task_definition import TaskDefinition, TaskPipeline, TaskStatus
from core.assets.workflows.task_queue import (
    CheckpointerFactory,
    TaskHandle,
    TaskQueue,
    TaskStatus as QueueTaskStatus,
)
from core.assets.workflows.node_operator import NodeIdentity, NodeLeaseManager, NodeOperator


# ── Section 1: Execution Loop, Runtime & Scanner ─────────────────────

def test_resolve_workspace_path_blocks_escape(temp_paths):
    assert resolve_workspace_path(str(temp_paths.workspace_dir), "../outside") is None


def test_analyze_python_error_extracts_missing_module():
    analysis = analyze_python_error("ModuleNotFoundError: No module named 'pandas'")

    assert analysis["type"] == "import_error"
    assert analysis["module"] == "pandas"


def test_exec_code_tool_returns_structured_name_error(temp_paths):
    temp_paths.workspace_dir.mkdir(parents=True, exist_ok=True)
    tool = ExecCodeTool(workspace_dir=str(temp_paths.workspace_dir))

    result = json.loads(tool._run("print(missing_name)", timeout=5))

    assert result["success"] is False
    assert result["error_analysis"]["type"] == "name_error"


def test_scan_project_tool_returns_stats_for_workspace_file(temp_paths):
    temp_paths.workspace_dir.mkdir(parents=True, exist_ok=True)
    (temp_paths.workspace_dir / "demo.txt").write_text("hello", encoding="utf-8")
    tool = ScanProjectTool(workspace_dir=str(temp_paths.workspace_dir))

    result = json.loads(tool._run())

    assert result["success"] is True
    assert result["stats"]["total_files"] == 1
    assert result["stats"]["by_extension"][".txt"] == 1


def test_scan_project_tool_blocks_path_escape(temp_paths):
    tool = ScanProjectTool(workspace_dir=str(temp_paths.workspace_dir))

    result = json.loads(tool._run(path="../outside"))

    assert result["success"] is False
    assert result["error"] == "路径不在 workspace 内"


def test_execution_runtime_blocks_workspace_escape(temp_paths):
    runtime = ExecutionRuntime(workspace_dir=str(temp_paths.workspace_dir))

    result = runtime.run_code(code="print('hi')", cwd="../outside")

    assert result["success"] is False
    assert result["error"] == "工作目录不在 workspace 内"


def test_execution_runtime_returns_structured_python_success(temp_paths):
    temp_paths.workspace_dir.mkdir(parents=True, exist_ok=True)
    runtime = ExecutionRuntime(workspace_dir=str(temp_paths.workspace_dir))

    result = runtime.run_code(code="print('hello runtime')", timeout=5)

    assert result["success"] is True
    assert "hello runtime" in result["stdout"]


def test_project_scanner_returns_stats_for_workspace_files(temp_paths):
    temp_paths.workspace_dir.mkdir(parents=True, exist_ok=True)
    (temp_paths.workspace_dir / "notes.txt").write_text("hello", encoding="utf-8")
    scanner = ProjectScanner(workspace_dir=str(temp_paths.workspace_dir))

    result = scanner.scan()

    assert result["success"] is True
    assert result["stats"]["total_files"] == 1
    assert result["stats"]["by_extension"][".txt"] == 1


def test_iterative_resource_validator_reports_missing_resource(temp_paths):
    validator = IterativeResourceValidator(workspace_dir=str(temp_paths.workspace_dir))

    result = validator.validate(resource_path="apps/missing-app")

    assert result["success"] is False
    assert "不存在" in result["error"]


def test_iterative_resource_validator_detects_html_and_api_failures(temp_paths):
    app_dir = temp_paths.workspace_dir / "apps" / "demo"
    app_dir.mkdir(parents=True, exist_ok=True)
    (app_dir / "index.html").write_text("", encoding="utf-8")
    (app_dir / "api.py").write_text("def broken(:\n    pass\n", encoding="utf-8")

    validator = IterativeResourceValidator(workspace_dir=str(temp_paths.workspace_dir))
    result = validator.validate(resource_path="apps/demo")

    assert result["success"] is True
    assert result["verdict"] == "FAIL"
    checks = {item["check"]: item for item in result["results"]}
    assert checks["html_empty"]["status"] == "fail"
    assert checks["api_syntax"]["status"] == "fail"


# ── Section 2: Execution Protocol & Task Runtime ─────────────────────

def test_waiting_approval_payload_normalizes_primary_and_pending_ids():
    payload = WaitingApprovalPayload.from_payload(
        {
            "status": "waiting_approval",
            "approval_id": "appr_primary",
            "approval_ids": ["appr_secondary", "appr_primary"],
            "pending_approvals": [
                {"approval_id": "appr_secondary", "agent_name": "reviewer", "task": "review"},
                {"approval_id": "appr_third", "agent_name": "security", "task": "scan"},
            ],
            "workflow_pause_kind": "delegated_subagent",
            "workflow_pause_mode": "consensus",
        }
    )

    assert payload.primary_approval_id == "appr_primary"
    assert payload.all_approval_ids == ("appr_primary", "appr_secondary", "appr_third")
    normalized = payload.to_payload({"status": "waiting_approval"})
    assert normalized["approval_ids"] == ["appr_primary", "appr_secondary", "appr_third"]
    assert normalized["pending_approvals"][1]["agent_name"] == "security"


def test_workflow_pause_metadata_attaches_resume_context_to_all_pending_requests():
    queue = ApprovalQueue()
    first = queue.create_request(kind="tool_call", scope="subagent:planner", summary="planner", prompt="allow 1?")
    second = queue.create_request(kind="tool_call", scope="subagent:reviewer", summary="reviewer", prompt="allow 2?")
    metadata = WorkflowPauseMetadata.from_waiting_payload(
        workflow_id="wf_consensus",
        workflow_name="consensus",
        node_id="consensus_node",
        node_label="Consensus",
        resume_token="resume-123",
        payload={
            "workflow_pause_kind": "delegated_subagent",
            "workflow_pause_mode": "consensus",
        },
    )

    prompt = attach_workflow_pause_metadata(
        approval_queue=queue,
        approval_ids=[first.approval_id, second.approval_id],
        primary_approval_id=first.approval_id,
        pause_metadata=metadata,
        default_prompt="fallback",
    )

    assert prompt == "allow 1?"
    assert queue.get_request(first.approval_id).metadata["workflow_resume_token"] == "resume-123"
    assert queue.get_request(second.approval_id).metadata["workflow_pause_mode"] == "consensus"


def test_approval_resolution_context_routes_subagent_tool_calls_and_resume_targets():
    queue = ApprovalQueue()
    request = queue.create_request(
        kind="tool_call",
        scope="subagent:helper",
        summary="helper",
        prompt="allow helper?",
        metadata={
            "target": "subagent:helper",
            "thread_id": "delegate-thread",
            "workflow_id": "wf_delegate",
            "workflow_resume_token": "resume-xyz",
            "workflow_pause_kind": "delegated_subagent",
        },
    )
    context = ApprovalResolutionContext.from_request(request)

    assert context.routes_to_system_agent is True
    assert context.workflow_pause.should_resume_delegated_subagent is True


def test_normalize_pending_approval_refs_preserves_extra_payload_fields():
    refs = normalize_pending_approval_refs(
        [
            {
                "approval_id": "appr_1",
                "agent_name": "reviewer",
                "task": "review",
                "payload": {"status": "waiting_approval"},
                "custom": "value",
            }
        ]
    )

    assert refs[0].to_dict()["payload"]["status"] == "waiting_approval"
    assert refs[0].to_dict()["custom"] == "value"


def test_task_runtime_service_tracks_tasks_and_recent_activity():
    runtime = TaskRuntimeService()
    runtime.upsert_tasks(
        [
            {"id": "t1", "content": "inspect tree", "status": "in_progress"},
            {"id": "t2", "content": "wire task runtime", "status": "pending"},
        ]
    )
    runtime.ingest_tool_runs(
        [
            {"title": "read_file", "run_id": "run-1", "status": "completed", "source": "tool_control"},
            {"title": "write_file", "run_id": "run-2", "status": "completed", "source": "tool_control"},
        ]
    )
    runtime.ingest_permission_events(
        [
            {"action": "set_mode", "mode": "plan", "timestamp": 2.0},
        ]
    )
    runtime.record_compaction_boundary(
        {
            "reason": "conversation_compaction",
            "summary": "compacted older transcript",
            "timestamp": 3.0,
        }
    )

    projection = runtime.build_projection()

    assert projection is not None
    assert [item["id"] for item in projection["tasks"]] == ["t1", "t2"]
    assert [item["kind"] for item in projection["activities"]] == ["tool_run", "tool_run", "governance", "compaction"]
    assert projection["status_counts"]["in_progress"] == 1
    assert projection["activity_counts"]["tool_run"] == 2
    assert projection["tasks"][0]["lifecycle"] == "foreground"
    assert projection["tasks"][0]["surface"] == "chat"
    assert projection["activities"][-1]["lifecycle"] == "resume"


# ── Section 3: Conditional Workflow Tasks ───────────────────────────

def _noop(*a, **kw):
    pass


def _make_runtime(exec_node=None):
    """Build a minimal WorkflowExecutionRuntime with mocked callbacks."""

    def default_exec(node, workflow, run_id):
        node.status = NodeStatus.COMPLETED
        node.started_at = time.time()
        node.completed_at = time.time()
        result = {"result": f"{node.id}_output"}
        node.output = result
        workflow.variables[f"{node.id}.output"] = result
        workflow.variables[f"{node.id}.status"] = "completed"
        return result

    def topo_sort(wf):
        return list(wf.nodes.keys())

    def get_ready(wf):
        ready = []
        for nid, node in wf.nodes.items():
            if node.status != NodeStatus.PENDING:
                continue
            predecessors = [e.source for e in wf.edges if e.target == nid]
            if all(
                wf.nodes[p].status in (NodeStatus.COMPLETED, NodeStatus.SKIPPED) for p in predecessors if p in wf.nodes
            ):
                ready.append(nid)
        return ready

    def get_successors(wf, node_id):
        return [e for e in wf.edges if e.source == node_id]

    def resolve_var(name, wf):
        return wf.variables.get(name)

    active_workflows = {}

    return WorkflowExecutionRuntime(
        approval_queue=MagicMock(),
        topo_sort=topo_sort,
        get_ready_nodes=get_ready,
        get_successors=get_successors,
        exec_node=exec_node or default_exec,
        resolve_var=resolve_var,
        register_active_workflow=lambda wf: active_workflows.update({wf.id: wf}),
        save_workflow=_noop,
        load_workflow=lambda wid: active_workflows.get(wid),
        get_active_workflow=lambda wid: active_workflows.get(wid),
        workflow_approval_fingerprint=lambda **kw: "fp",
        log_event=_noop,
        process_node_success=None,
    )


def _make_workflow(nodes_spec, edges_spec, variables=None):
    """Create a WorkflowDef from compact specs.

    nodes_spec: list of (id, type, skip_condition_or_None)
    edges_spec: list of (source, target)
    """
    nodes = {}
    for spec in nodes_spec:
        nid, ntype = spec[0], spec[1]
        cond = spec[2] if len(spec) > 2 else None
        nodes[nid] = FlowNode(id=nid, type=ntype, skip_condition=cond)

    edges = [FlowEdge(id=f"e_{s}_{t}", source=s, target=t) for s, t in edges_spec]

    wf = WorkflowDef(id="test_wf", name="test", nodes=nodes, edges=edges)
    if variables:
        wf.variables.update(variables)
    return wf


class TestConditionEvaluator:
    def test_true_condition(self):
        wf = _make_workflow(
            [("n1", NodeType.EXEC, "x > 5")],
            [],
            variables={"x": 10},
        )
        result = WorkflowExecutionRuntime._evaluate_skip_condition(wf.nodes["n1"], wf)
        assert result is True

    def test_false_condition(self):
        wf = _make_workflow(
            [("n1", NodeType.EXEC, "x > 5")],
            [],
            variables={"x": 3},
        )
        result = WorkflowExecutionRuntime._evaluate_skip_condition(wf.nodes["n1"], wf)
        assert result is False

    def test_no_condition(self):
        wf = _make_workflow([("n1", NodeType.EXEC)], [])
        result = WorkflowExecutionRuntime._evaluate_skip_condition(wf.nodes["n1"], wf)
        assert result is True

    def test_len_function(self):
        wf = _make_workflow(
            [("n1", NodeType.EXEC, "len(items) > 0")],
            [],
            variables={"items": [1, 2, 3]},
        )
        assert WorkflowExecutionRuntime._evaluate_skip_condition(wf.nodes["n1"], wf) is True

    def test_empty_list_condition(self):
        wf = _make_workflow(
            [("n1", NodeType.EXEC, "len(items) > 0")],
            [],
            variables={"items": []},
        )
        assert WorkflowExecutionRuntime._evaluate_skip_condition(wf.nodes["n1"], wf) is False

    def test_forbidden_import(self):
        wf = _make_workflow(
            [("n1", NodeType.EXEC, "import os")],
            [],
        )
        assert WorkflowExecutionRuntime._evaluate_skip_condition(wf.nodes["n1"], wf) is True

    def test_forbidden_eval(self):
        wf = _make_workflow(
            [("n1", NodeType.EXEC, "eval('1+1')")],
            [],
        )
        assert WorkflowExecutionRuntime._evaluate_skip_condition(wf.nodes["n1"], wf) is True

    def test_forbidden_dunder(self):
        wf = _make_workflow(
            [("n1", NodeType.EXEC, "__builtins__")],
            [],
        )
        assert WorkflowExecutionRuntime._evaluate_skip_condition(wf.nodes["n1"], wf) is True

    def test_variable_with_dots(self):
        wf = _make_workflow(
            [("n1", NodeType.EXEC, "step1_output > 0")],
            [],
        )
        wf.variables["step1.output"] = 42
        assert WorkflowExecutionRuntime._evaluate_skip_condition(wf.nodes["n1"], wf) is True

    def test_syntax_error_defaults_to_execute(self):
        wf = _make_workflow(
            [("n1", NodeType.EXEC, "this is not valid python!!!")],
            [],
        )
        assert WorkflowExecutionRuntime._evaluate_skip_condition(wf.nodes["n1"], wf) is True


class TestConditionalWorkflowExecution:
    def test_condition_true_executes(self):
        wf = _make_workflow(
            [
                ("_start", NodeType.START),
                ("step1", NodeType.EXEC, "True"),
                ("_end", NodeType.END),
            ],
            [("_start", "step1"), ("step1", "_end")],
        )
        wf.nodes["_start"].status = NodeStatus.COMPLETED

        runtime = _make_runtime()
        runtime.run_workflow(wf)
        assert wf.nodes["step1"].status == NodeStatus.COMPLETED

    def test_condition_false_skips(self):
        wf = _make_workflow(
            [
                ("_start", NodeType.START),
                ("step1", NodeType.EXEC, "False"),
                ("_end", NodeType.END),
            ],
            [("_start", "step1"), ("step1", "_end")],
        )
        wf.nodes["_start"].status = NodeStatus.COMPLETED

        runtime = _make_runtime()
        runtime.run_workflow(wf)
        assert wf.nodes["step1"].status == NodeStatus.SKIPPED
        assert wf.nodes["step1"].output == {"skipped": True, "reason": "condition not met"}

    def test_no_condition_always_executes(self):
        wf = _make_workflow(
            [
                ("_start", NodeType.START),
                ("step1", NodeType.EXEC),
                ("_end", NodeType.END),
            ],
            [("_start", "step1"), ("step1", "_end")],
        )
        wf.nodes["_start"].status = NodeStatus.COMPLETED

        runtime = _make_runtime()
        runtime.run_workflow(wf)
        assert wf.nodes["step1"].status == NodeStatus.COMPLETED

    def test_condition_uses_variables(self):
        wf = _make_workflow(
            [
                ("_start", NodeType.START),
                ("step1", NodeType.EXEC, "score > 80"),
                ("_end", NodeType.END),
            ],
            [("_start", "step1"), ("step1", "_end")],
            variables={"score": 90},
        )
        wf.nodes["_start"].status = NodeStatus.COMPLETED

        runtime = _make_runtime()
        runtime.run_workflow(wf)
        assert wf.nodes["step1"].status == NodeStatus.COMPLETED

    def test_skipped_node_output_in_variables(self):
        wf = _make_workflow(
            [
                ("_start", NodeType.START),
                ("step1", NodeType.EXEC, "False"),
                ("_end", NodeType.END),
            ],
            [("_start", "step1"), ("step1", "_end")],
        )
        wf.nodes["_start"].status = NodeStatus.COMPLETED

        runtime = _make_runtime()
        runtime.run_workflow(wf)
        assert wf.variables.get("step1.output") == {"skipped": True, "reason": "condition not met"}
        assert wf.variables.get("step1.status") == "skipped"

    def test_start_and_end_never_conditionally_skipped(self):
        wf = _make_workflow(
            [
                ("_start", NodeType.START, "False"),
                ("step1", NodeType.EXEC),
                ("_end", NodeType.END, "False"),
            ],
            [("_start", "step1"), ("step1", "_end")],
        )
        runtime = _make_runtime()
        runtime.run_workflow(wf)
        assert wf.nodes["_start"].status != NodeStatus.SKIPPED


# ── Section 4: Task Definition & Task Pipeline ───────────────────────

class ResearchResult(BaseModel):
    summary: str = Field(description="Research summary")
    findings: list[str] = Field(description="Key findings")


class TestTaskDefinition:
    def test_build_prompt_basic(self):
        task = TaskDefinition(
            name="research",
            description="Research AI trends",
            expected_output="A summary with findings",
        )
        prompt = task.build_prompt()
        assert "research" in prompt.lower()
        assert "Expected Output" in prompt

    def test_build_prompt_with_schema(self):
        task = TaskDefinition(
            name="research",
            description="Research AI trends",
            expected_output="A JSON result",
            output_schema=ResearchResult,
        )
        prompt = task.build_prompt()
        assert "Output Schema" in prompt
        assert "summary" in prompt

    def test_build_prompt_with_context(self):
        task = TaskDefinition(
            name="write_report",
            description="Write report",
            expected_output="A report",
            context_from=["research"],
        )
        prompt = task.build_prompt(context={"research": "AI is growing fast"})
        assert "Context from Previous Tasks" in prompt
        assert "AI is growing fast" in prompt

    def test_validate_output_no_schema(self):
        task = TaskDefinition(name="t", description="d", expected_output="e")
        ok, err = task.validate_output("any string")
        assert ok is True

    def test_validate_output_dict(self):
        task = TaskDefinition(
            name="t", description="d", expected_output="e",
            output_schema=ResearchResult,
        )
        ok, err = task.validate_output({"summary": "s", "findings": ["a"]})
        assert ok is True

    def test_validate_output_json_string(self):
        task = TaskDefinition(
            name="t", description="d", expected_output="e",
            output_schema=ResearchResult,
        )
        ok, err = task.validate_output(json.dumps({"summary": "s", "findings": ["a"]}))
        assert ok is True

    def test_validate_output_invalid(self):
        task = TaskDefinition(
            name="t", description="d", expected_output="e",
            output_schema=ResearchResult,
        )
        ok, err = task.validate_output({"summary": "s"})
        assert ok is False
        assert "findings" in err

    def test_validate_output_wrong_type(self):
        task = TaskDefinition(
            name="t", description="d", expected_output="e",
            output_schema=ResearchResult,
        )
        ok, err = task.validate_output(42)
        assert ok is False

    def test_lifecycle(self):
        task = TaskDefinition(name="t", description="d", expected_output="e")
        assert task.status == TaskStatus.PENDING
        task.mark_started()
        assert task.status == TaskStatus.IN_PROGRESS
        assert task.attempt == 1
        task.mark_completed("done")
        assert task.status == TaskStatus.COMPLETED
        assert task.result == "done"
        assert task.elapsed_seconds is not None

    def test_can_retry(self):
        task = TaskDefinition(name="t", description="d", expected_output="e", max_retries=2)
        assert task.can_retry is True
        task.mark_started()
        task.mark_started()
        task.mark_started()
        assert task.can_retry is False

    def test_to_dict(self):
        task = TaskDefinition(
            name="research",
            description="Research AI",
            expected_output="Summary",
            agent_name="researcher",
            output_schema=ResearchResult,
        )
        d = task.to_dict()
        assert d["name"] == "research"
        assert d["agent_name"] == "researcher"
        assert d["output_schema"] == "ResearchResult"
        assert d["status"] == "pending"


class TestTaskPipeline:
    def test_sequential_execution(self):
        task1 = TaskDefinition(name="step1", description="Do step 1", expected_output="result1")
        task2 = TaskDefinition(
            name="step2", description="Do step 2",
            expected_output="result2", context_from=["step1"],
        )

        def execute_fn(prompt: str, agent_name=None):
            if "step1" in prompt.lower():
                return "step1 output"
            return "step2 output"

        pipeline = TaskPipeline(tasks=[task1, task2])
        results = pipeline.run(execute_fn)
        assert "step1" in results
        assert "step2" in results
        assert task1.status == TaskStatus.COMPLETED
        assert task2.status == TaskStatus.COMPLETED

    def test_stop_on_failure(self):
        task1 = TaskDefinition(name="fail_task", description="Will fail", expected_output="x")
        task2 = TaskDefinition(name="skip_task", description="Skipped", expected_output="y")

        def execute_fn(prompt, agent_name=None):
            raise RuntimeError("boom")

        pipeline = TaskPipeline(tasks=[task1, task2])
        results = pipeline.run(execute_fn, stop_on_failure=True)
        assert len(results) == 0
        assert task1.status == TaskStatus.FAILED
        assert task2.status == TaskStatus.PENDING

    def test_continue_on_failure(self):
        call_count = 0

        def execute_fn(prompt, agent_name=None):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise RuntimeError("first fails")
            return "ok"

        task1 = TaskDefinition(name="t1", description="d1", expected_output="e1")
        task2 = TaskDefinition(name="t2", description="d2", expected_output="e2")
        pipeline = TaskPipeline(tasks=[task1, task2])
        results = pipeline.run(execute_fn, stop_on_failure=False)
        assert task1.status == TaskStatus.FAILED
        assert task2.status == TaskStatus.COMPLETED
        assert "t2" in results

    def test_output_validation_retry(self):
        call_count = 0

        def execute_fn(prompt, agent_name=None):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return '{"summary": "s"}'
            return '{"summary": "s", "findings": ["a"]}'

        task = TaskDefinition(
            name="validated",
            description="d",
            expected_output="JSON",
            output_schema=ResearchResult,
            max_retries=1,
        )
        pipeline = TaskPipeline(tasks=[task])
        pipeline.run(execute_fn)
        assert task.status == TaskStatus.COMPLETED
        assert call_count == 2

    def test_summary(self):
        task = TaskDefinition(name="t", description="d", expected_output="e")
        pipeline = TaskPipeline(tasks=[task])
        summary = pipeline.summary()
        assert len(summary) == 1
        assert summary[0]["name"] == "t"


# ── Section 5: Task Queue ────────────────────────────────────────────

@pytest.fixture
def task_queue_fixture():
    q = TaskQueue(max_workers=2, max_history=50)
    yield q
    q.shutdown(wait=True)


class TestTaskSubmit:
    def test_submit_returns_handle(self, task_queue_fixture):
        handle = task_queue_fixture.submit(lambda: 42, name="answer")
        assert isinstance(handle, TaskHandle)
        assert handle.name == "answer"
        assert handle.task_id

    def test_submit_executes_task(self, task_queue_fixture):
        handle = task_queue_fixture.submit(lambda: "done", name="simple")
        info = task_queue_fixture.wait(handle.task_id, timeout=5)
        assert info is not None
        assert info.status == QueueTaskStatus.COMPLETED
        assert info.result == "done"

    def test_submit_with_args(self, task_queue_fixture):
        def add(a, b):
            return a + b

        handle = task_queue_fixture.submit(add, 3, 4, name="add")
        info = task_queue_fixture.wait(handle.task_id, timeout=5)
        assert info is not None
        assert info.result == 7

    def test_submit_with_kwargs(self, task_queue_fixture):
        def greet(name="world"):
            return f"hello {name}"

        handle = task_queue_fixture.submit(greet, name="greet", metadata={"priority": "high"})
        info = task_queue_fixture.wait(handle.task_id, timeout=5)
        assert info is not None
        assert info.result == "hello world"

    def test_failed_task(self, task_queue_fixture):
        def fail():
            raise ValueError("intentional")

        handle = task_queue_fixture.submit(fail, name="failing")
        info = task_queue_fixture.wait(handle.task_id, timeout=5)
        assert info is not None
        assert info.status == QueueTaskStatus.FAILED
        assert "intentional" in info.error

    def test_task_has_timestamps(self, task_queue_fixture):
        handle = task_queue_fixture.submit(lambda: time.sleep(0.01), name="timed")
        info = task_queue_fixture.wait(handle.task_id, timeout=5)
        assert info is not None
        assert info.created_at > 0
        assert info.started_at is not None
        assert info.completed_at is not None
        assert info.started_at >= info.created_at
        assert info.completed_at >= info.started_at


class TestTaskStatus:
    def test_get_status_unknown(self, task_queue_fixture):
        assert task_queue_fixture.get_status("nonexistent") is None

    def test_get_status_after_submit(self, task_queue_fixture):
        handle = task_queue_fixture.submit(lambda: time.sleep(0.5), name="slow")
        info = task_queue_fixture.get_status(handle.task_id)
        assert info is not None
        assert info.status in (QueueTaskStatus.PENDING, QueueTaskStatus.RUNNING)
        task_queue_fixture.wait(handle.task_id, timeout=5)


class TestTaskCancel:
    def test_cancel_completed(self, task_queue_fixture):
        handle = task_queue_fixture.submit(lambda: 1, name="fast")
        task_queue_fixture.wait(handle.task_id, timeout=5)
        assert task_queue_fixture.cancel(handle.task_id) is False

    def test_cancel_nonexistent(self, task_queue_fixture):
        assert task_queue_fixture.cancel("nope") is False


class TestTaskList:
    def test_list_active(self, task_queue_fixture):
        event = threading.Event()
        handle = task_queue_fixture.submit(lambda: event.wait(5), name="blocking")
        time.sleep(0.1)
        active = task_queue_fixture.list_active()
        assert len(active) >= 1
        assert any(t.task_id == handle.task_id for t in active)
        event.set()
        task_queue_fixture.wait(handle.task_id, timeout=5)

    def test_list_all(self, task_queue_fixture):
        task_queue_fixture.submit(lambda: 1, name="t1")
        task_queue_fixture.submit(lambda: 2, name="t2")
        time.sleep(0.5)
        all_tasks = task_queue_fixture.list_all()
        assert len(all_tasks) >= 2

    def test_summary(self, task_queue_fixture):
        task_queue_fixture.submit(lambda: 1, name="t1")
        task_queue_fixture.submit(lambda: 2, name="t2")
        time.sleep(0.5)
        summary = task_queue_fixture.get_summary()
        assert isinstance(summary, dict)


class TestTaskWait:
    def test_wait_with_timeout(self, task_queue_fixture):
        event = threading.Event()
        handle = task_queue_fixture.submit(lambda: event.wait(10), name="long")
        info = task_queue_fixture.wait(handle.task_id, timeout=0.1)
        assert info is not None
        assert info.status in (QueueTaskStatus.PENDING, QueueTaskStatus.RUNNING)
        event.set()
        task_queue_fixture.wait(handle.task_id, timeout=5)

    def test_wait_nonexistent(self, task_queue_fixture):
        result = task_queue_fixture.wait("nope", timeout=0.1)
        assert result is None


class TestPruneHistory:
    def test_prune_keeps_within_limit(self):
        q = TaskQueue(max_workers=1, max_history=5)
        for i in range(10):
            h = q.submit(lambda x=i: x, name=f"task_{i}")
            q.wait(h.task_id, timeout=5)

        all_tasks = q.list_all()
        assert len(all_tasks) <= 6
        q.shutdown()


class TestCheckpointerFactory:
    def test_sqlite_default(self, tmp_path):
        db_path = str(tmp_path / "test.db")
        saver = CheckpointerFactory.create({"type": "sqlite", "path": db_path})
        assert saver is not None

    def test_sqlite_no_config(self):
        saver = CheckpointerFactory.create()
        assert saver is not None

    def test_postgres_falls_back_to_sqlite(self):
        saver = CheckpointerFactory.create(
            {
                "type": "postgres",
                "connection_string": "postgresql://user:pass@localhost/db",
            }
        )
        assert saver is not None


# ── Section 6: Node Operator & Leases ────────────────────────────────

def test_node_identity_fqdn():
    identity = NodeIdentity(workflow_id="wf1", run_id="r1", node_id="n1", attempt=2)
    assert identity.fqdn == "wf1::r1::n1::a2"


def test_lease_manager_acquires_and_releases():
    manager = NodeLeaseManager()
    identity = NodeIdentity(workflow_id="wf1", run_id="r1", node_id="n1")

    lease1 = manager.acquire(identity, ttl_seconds=10)
    assert lease1 is not None

    lease2 = manager.acquire(identity, ttl_seconds=10)
    assert lease2 is None  # Already leased

    released = manager.release(identity, lease1.lease_id)
    assert released is True

    lease3 = manager.acquire(identity, ttl_seconds=10)
    assert lease3 is not None  # Can lease again after release


def test_node_operator_successful_execution():
    operator = NodeOperator()
    node = FlowNode(id="n1", type=NodeType.EXEC)
    workflow = WorkflowDef(id="wf1", name="test")

    def dispatch(n, w):
        return {"result": "success"}

    events = []

    def log_event(w, n_id, evt, detail):
        events.append((evt, detail))

    result = operator.invoke(node, workflow, "run1", dispatch, log_event)

    assert result == {"result": "success"}
    assert node.status == NodeStatus.COMPLETED
    assert ("start", "type=exec") in events
    assert any(e[0] == "completed" for e in events)


def test_node_operator_timeout():
    operator = NodeOperator()
    node = FlowNode(
        id="n1",
        type=NodeType.EXEC,
        exception_config=NodeExceptionConfig(timeout_seconds=0.1),
    )
    workflow = WorkflowDef(id="wf1", name="test")

    def dispatch(n, w):
        time.sleep(0.5)
        return {"result": "success"}

    with pytest.raises(TimeoutError, match="timed out after 0.1s"):
        operator.invoke(node, workflow, "run1", dispatch, lambda *args: None)

    assert node.status == NodeStatus.FAILED


def test_node_operator_retry():
    operator = NodeOperator()
    node = FlowNode(
        id="n1",
        type=NodeType.EXEC,
        exception_config=NodeExceptionConfig(max_retries=2, retry_delay=0.1),
    )
    workflow = WorkflowDef(id="wf1", name="test")

    attempts = 0

    def dispatch(n, w):
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            raise ValueError("Temporary error")
        return {"result": "success"}

    events = []

    def log_event(w, n_id, evt, detail):
        events.append(evt)

    result = operator.invoke(node, workflow, "run1", dispatch, log_event)

    assert result == {"result": "success"}
    assert attempts == 3
    assert events.count("retry") == 2
    assert node.status == NodeStatus.COMPLETED


def test_node_operator_fallback():
    operator = NodeOperator()
    node = FlowNode(
        id="n1",
        type=NodeType.EXEC,
        exception_config=NodeExceptionConfig(fallback_output={"fallback": True}),
    )
    workflow = WorkflowDef(id="wf1", name="test")

    def dispatch(n, w):
        raise ValueError("Fatal error")

    events = []

    def log_event(w, n_id, evt, detail):
        events.append(evt)

    result = operator.invoke(node, workflow, "run1", dispatch, log_event)

    assert result == {"fallback": True}
    assert node.status == NodeStatus.COMPLETED
    assert "fallback" in events


def test_node_operator_idempotency():
    operator = NodeOperator()
    node = FlowNode(
        id="n1",
        type=NodeType.EXEC,
        idempotency_key="my_key",
    )
    workflow = WorkflowDef(id="wf1", name="test", variables={"_idempotent:my_key": {"cached": True}})

    attempts = 0

    def dispatch(n, w):
        nonlocal attempts
        attempts += 1
        return {"result": "success"}

    events = []

    def log_event(w, n_id, evt, detail):
        events.append(evt)

    result = operator.invoke(node, workflow, "run1", dispatch, log_event)

    assert result == {"cached": True}
    assert attempts == 0  # Dispatch not called
    assert "idempotent_hit" in events
    assert node.status == NodeStatus.COMPLETED
