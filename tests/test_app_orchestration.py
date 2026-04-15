"""Tests for the App Orchestration Registry."""

import json
import tempfile
from pathlib import Path

import pytest

from core.modes.apps.app_orchestration import (
    AppOrchestrationRegistry,
    BindingDirection,
    DataBinding,
    NodeStatus,
    NodeType,
    OrchestrationNode,
    OrchestrationPipeline,
)

# --- Enum tests ---

class TestEnums:
    def test_node_type_values(self):
        assert NodeType.APP.value == "app"
        assert NodeType.WORKFLOW.value == "workflow"
        assert NodeType.AGENT.value == "agent"
        assert NodeType.TOOL.value == "tool"
        assert NodeType.EXTERNAL.value == "external"

    def test_binding_direction_values(self):
        assert BindingDirection.INPUT.value == "input"
        assert BindingDirection.OUTPUT.value == "output"
        assert BindingDirection.BIDIRECTIONAL.value == "bidirectional"

    def test_node_status_values(self):
        assert NodeStatus.ACTIVE.value == "active"
        assert NodeStatus.INACTIVE.value == "inactive"
        assert NodeStatus.ERROR.value == "error"
        assert NodeStatus.PENDING.value == "pending"


# --- DataBinding tests ---

class TestDataBinding:
    def test_to_dict_minimal(self):
        b = DataBinding("a", "out", "b", "in")
        d = b.to_dict()
        assert d["source_node"] == "a"
        assert d["target_node"] == "b"
        assert "transform" not in d
        assert "description" not in d

    def test_to_dict_with_optional(self):
        b = DataBinding("a", "out", "b", "in", transform="json_extract(.x)", description="extract x")
        d = b.to_dict()
        assert d["transform"] == "json_extract(.x)"
        assert d["description"] == "extract x"

    def test_roundtrip(self):
        b = DataBinding("s", "p1", "t", "p2", direction=BindingDirection.BIDIRECTIONAL, transform="t")
        d = b.to_dict()
        b2 = DataBinding.from_dict(d)
        assert b2.source_node == "s"
        assert b2.target_node == "t"
        assert b2.direction == BindingDirection.BIDIRECTIONAL
        assert b2.transform == "t"


# --- OrchestrationNode tests ---

class TestOrchestrationNode:
    def test_roundtrip(self):
        n = OrchestrationNode(
            node_id="n1",
            name="my_app",
            node_type=NodeType.APP,
            description="desc",
            domain="finance",
            owner="admin",
            input_ports=["data_in"],
            output_ports=["report_out"],
            metadata={"key": "val"},
        )
        d = n.to_dict()
        n2 = OrchestrationNode.from_dict(d)
        assert n2.node_id == "n1"
        assert n2.name == "my_app"
        assert n2.node_type == NodeType.APP
        assert n2.domain == "finance"
        assert n2.input_ports == ["data_in"]
        assert n2.output_ports == ["report_out"]
        assert n2.metadata == {"key": "val"}

    def test_defaults(self):
        n = OrchestrationNode(node_id="x", name="x", node_type=NodeType.TOOL)
        assert n.input_ports == ["default"]
        assert n.output_ports == ["default"]
        assert n.status == NodeStatus.ACTIVE


# --- OrchestrationPipeline tests ---

class TestOrchestrationPipeline:
    def test_roundtrip(self):
        p = OrchestrationPipeline(
            pipeline_id="p1",
            name="daily_sync",
            description="sync data",
            steps=["n1", "n2", "n3"],
            schedule="0 8 * * *",
        )
        d = p.to_dict()
        p2 = OrchestrationPipeline.from_dict(d)
        assert p2.pipeline_id == "p1"
        assert p2.name == "daily_sync"
        assert p2.steps == ["n1", "n2", "n3"]
        assert p2.schedule == "0 8 * * *"


# --- Registry core tests ---

class TestRegistryNodes:
    def test_register_and_get(self):
        reg = AppOrchestrationRegistry()
        node = reg.register_node("app_a", NodeType.APP, description="first app", node_id="a1")
        assert node.node_id == "a1"
        assert node.name == "app_a"
        fetched = reg.get_node("a1")
        assert fetched is not None
        assert fetched.name == "app_a"

    def test_register_with_string_type(self):
        reg = AppOrchestrationRegistry()
        node = reg.register_node("wf", "workflow")
        assert node.node_type == NodeType.WORKFLOW

    def test_unregister_node(self):
        reg = AppOrchestrationRegistry()
        reg.register_node("a", NodeType.APP, node_id="a1")
        assert reg.unregister_node("a1") is True
        assert reg.get_node("a1") is None
        assert reg.unregister_node("nonexistent") is False

    def test_unregister_cascades_bindings(self):
        reg = AppOrchestrationRegistry()
        reg.register_node("a", NodeType.APP, node_id="a1")
        reg.register_node("b", NodeType.APP, node_id="b1")
        reg.add_binding("a1", "default", "b1", "default")
        assert len(reg.list_bindings()) == 1
        reg.unregister_node("a1")
        assert len(reg.list_bindings()) == 0

    def test_find_node_by_name(self):
        reg = AppOrchestrationRegistry()
        reg.register_node("x_app", NodeType.APP, node_id="x1")
        found = reg.find_node_by_name("x_app")
        assert found is not None
        assert found.node_id == "x1"
        assert reg.find_node_by_name("missing") is None

    def test_list_nodes_filter_by_type(self):
        reg = AppOrchestrationRegistry()
        reg.register_node("a1", NodeType.APP)
        reg.register_node("a2", NodeType.APP)
        reg.register_node("w1", NodeType.WORKFLOW)
        apps = reg.list_nodes(node_type=NodeType.APP)
        assert len(apps) == 2
        wfs = reg.list_nodes(node_type="workflow")
        assert len(wfs) == 1

    def test_list_nodes_filter_by_domain(self):
        reg = AppOrchestrationRegistry()
        reg.register_node("a", NodeType.APP, domain="sales")
        reg.register_node("b", NodeType.APP, domain="hr")
        sales = reg.list_nodes(domain="sales")
        assert len(sales) == 1
        assert sales[0].domain == "sales"

    def test_list_nodes_filter_by_status(self):
        reg = AppOrchestrationRegistry()
        n = reg.register_node("a", NodeType.APP)
        reg.update_node_status(n.node_id, NodeStatus.ERROR)
        errors = reg.list_nodes(status="error")
        assert len(errors) == 1

    def test_update_status(self):
        reg = AppOrchestrationRegistry()
        reg.register_node("a", NodeType.APP, node_id="a1")
        assert reg.update_node_status("a1", "inactive") is True
        assert reg.get_node("a1").status == NodeStatus.INACTIVE
        assert reg.update_node_status("missing", "active") is False

    def test_update_metadata(self):
        reg = AppOrchestrationRegistry()
        reg.register_node("a", NodeType.APP, node_id="a1")
        reg.update_node_metadata("a1", version="2.0", author="me")
        node = reg.get_node("a1")
        assert node.metadata["version"] == "2.0"
        assert node.metadata["author"] == "me"
        assert reg.update_node_metadata("missing", k="v") is False


# --- Registry binding tests ---

class TestRegistryBindings:
    def test_add_binding(self):
        reg = AppOrchestrationRegistry()
        reg.register_node("a", NodeType.APP, node_id="a1")
        reg.register_node("b", NodeType.APP, node_id="b1")
        b = reg.add_binding("a1", "default", "b1", "default", description="a→b")
        assert b.source_node == "a1"
        assert b.description == "a→b"

    def test_add_binding_missing_node_raises(self):
        reg = AppOrchestrationRegistry()
        reg.register_node("a", NodeType.APP, node_id="a1")
        with pytest.raises(KeyError, match="Target node"):
            reg.add_binding("a1", "default", "missing", "default")
        with pytest.raises(KeyError, match="Source node"):
            reg.add_binding("missing", "default", "a1", "default")

    def test_remove_binding(self):
        reg = AppOrchestrationRegistry()
        reg.register_node("a", NodeType.APP, node_id="a1")
        reg.register_node("b", NodeType.APP, node_id="b1")
        reg.add_binding("a1", "default", "b1", "default")
        removed = reg.remove_binding("a1", "b1")
        assert removed == 1
        assert len(reg.list_bindings()) == 0

    def test_list_bindings_by_node(self):
        reg = AppOrchestrationRegistry()
        reg.register_node("a", NodeType.APP, node_id="a1")
        reg.register_node("b", NodeType.APP, node_id="b1")
        reg.register_node("c", NodeType.APP, node_id="c1")
        reg.add_binding("a1", "default", "b1", "default")
        reg.add_binding("b1", "default", "c1", "default")
        a_bindings = reg.list_bindings("a1")
        assert len(a_bindings) == 1
        b_bindings = reg.list_bindings("b1")
        assert len(b_bindings) == 2

    def test_upstream_downstream(self):
        reg = AppOrchestrationRegistry()
        reg.register_node("source", NodeType.APP, node_id="s")
        reg.register_node("middle", NodeType.WORKFLOW, node_id="m")
        reg.register_node("sink", NodeType.APP, node_id="k")
        reg.add_binding("s", "default", "m", "default")
        reg.add_binding("m", "default", "k", "default")
        upstream = reg.get_upstream("m")
        assert len(upstream) == 1
        assert upstream[0].node_id == "s"
        downstream = reg.get_downstream("m")
        assert len(downstream) == 1
        assert downstream[0].node_id == "k"


# --- Pipeline tests ---

class TestRegistryPipelines:
    def test_register_pipeline(self):
        reg = AppOrchestrationRegistry()
        reg.register_node("a", NodeType.APP, node_id="a1")
        reg.register_node("b", NodeType.APP, node_id="b1")
        p = reg.register_pipeline("sync", ["a1", "b1"], schedule="0 * * * *", pipeline_id="p1")
        assert p.pipeline_id == "p1"
        assert p.steps == ["a1", "b1"]

    def test_unregister_pipeline(self):
        reg = AppOrchestrationRegistry()
        reg.register_pipeline("test", [], pipeline_id="p1")
        assert reg.unregister_pipeline("p1") is True
        assert reg.unregister_pipeline("p1") is False

    def test_list_and_get_pipelines(self):
        reg = AppOrchestrationRegistry()
        reg.register_pipeline("a", [], pipeline_id="p1")
        reg.register_pipeline("b", [], pipeline_id="p2")
        assert len(reg.list_pipelines()) == 2
        assert reg.get_pipeline("p1").name == "a"
        assert reg.get_pipeline("missing") is None


# --- Topology and validation ---

class TestTopologyAndValidation:
    def _build_sample(self) -> AppOrchestrationRegistry:
        reg = AppOrchestrationRegistry()
        reg.register_node("data_source", NodeType.EXTERNAL, node_id="ds", domain="data")
        reg.register_node("etl_flow", NodeType.WORKFLOW, node_id="etl", domain="data")
        reg.register_node("dashboard", NodeType.APP, node_id="dash", domain="reporting")
        reg.add_binding("ds", "default", "etl", "default")
        reg.add_binding("etl", "default", "dash", "default")
        reg.register_pipeline("daily_report", ["ds", "etl", "dash"], pipeline_id="dr")
        return reg

    def test_get_topology(self):
        reg = self._build_sample()
        topo = reg.get_topology()
        assert topo["stats"]["total_nodes"] == 3
        assert topo["stats"]["total_bindings"] == 2
        assert topo["stats"]["total_pipelines"] == 1
        assert topo["stats"]["by_type"]["app"] == 1
        assert topo["stats"]["by_domain"]["data"] == 2

    def test_get_node_summary(self):
        reg = self._build_sample()
        summary = reg.get_node_summary("etl")
        assert summary is not None
        assert summary["node"]["name"] == "etl_flow"
        assert "data_source" in summary["upstream"]
        assert "dashboard" in summary["downstream"]
        assert reg.get_node_summary("missing") is None

    def test_validate_clean_graph(self):
        reg = self._build_sample()
        issues = reg.validate_graph()
        assert len(issues) == 0

    def test_validate_detects_orphan(self):
        reg = AppOrchestrationRegistry()
        reg.register_node("lonely", NodeType.APP, node_id="l1")
        issues = reg.validate_graph()
        assert any("Orphan" in i for i in issues)

    def test_validate_detects_bad_port(self):
        reg = AppOrchestrationRegistry()
        reg.register_node("a", NodeType.APP, node_id="a1", output_ports=["out1"])
        reg.register_node("b", NodeType.APP, node_id="b1", input_ports=["in1"])
        reg.add_binding("a1", "wrong_port", "b1", "in1")
        issues = reg.validate_graph()
        assert any("wrong_port" in i for i in issues)

    def test_validate_detects_missing_pipeline_node(self):
        reg = AppOrchestrationRegistry()
        reg.register_pipeline("broken", ["missing_node"], pipeline_id="bp")
        issues = reg.validate_graph()
        assert any("missing_node" in i for i in issues)


# --- Persistence tests ---

class TestPersistence:
    def test_save_and_load(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "orch.json"
            reg = AppOrchestrationRegistry(storage_path=path)
            reg.register_node("a", NodeType.APP, node_id="a1", domain="sales")
            reg.register_node("b", NodeType.WORKFLOW, node_id="b1")
            reg.add_binding("a1", "default", "b1", "default")
            reg.register_pipeline("pipe", ["a1", "b1"], pipeline_id="p1")

            reg2 = AppOrchestrationRegistry(storage_path=path)
            assert reg2.get_node("a1") is not None
            assert reg2.get_node("a1").domain == "sales"
            assert len(reg2.list_bindings()) == 1
            assert reg2.get_pipeline("p1") is not None

    def test_auto_save_on_mutation(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "orch.json"
            reg = AppOrchestrationRegistry(storage_path=path)
            reg.register_node("x", NodeType.TOOL, node_id="x1")
            assert path.exists()
            data = json.loads(path.read_text("utf-8"))
            assert "x1" in data["nodes"]

    def test_clear(self):
        reg = AppOrchestrationRegistry()
        reg.register_node("a", NodeType.APP, node_id="a1")
        reg.register_node("b", NodeType.APP, node_id="b1")
        reg.add_binding("a1", "default", "b1", "default")
        reg.register_pipeline("p", [], pipeline_id="p1")
        reg.clear()
        assert len(reg.list_nodes()) == 0
        assert len(reg.list_bindings()) == 0
        assert len(reg.list_pipelines()) == 0
