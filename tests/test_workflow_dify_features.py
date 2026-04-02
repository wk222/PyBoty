"""Tests for Dify-inspired workflow features: new nodes, edge states, variable templates."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from core.assets.workflows.workflow_graph_runtime import WorkflowGraphRuntime
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
)
from core.assets.workflows import (
    run_http_request,
    run_list_operator,
    run_parameter_extractor,
    run_question_classifier,
    run_variable_assigner,
)


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
    @patch("core.workflow_nodes_extended.urlopen")
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
