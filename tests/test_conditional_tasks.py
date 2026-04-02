"""Tests for conditional task execution in workflows."""

from __future__ import annotations

import time
from unittest.mock import MagicMock

from core.assets.workflows.workflow_execution_runtime import WorkflowExecutionRuntime
from core.assets.workflows.workflow_models import (
    FlowEdge,
    FlowNode,
    NodeStatus,
    NodeType,
    WorkflowDef,
)


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
        # Should return True (don't skip) when expression is suspicious
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
