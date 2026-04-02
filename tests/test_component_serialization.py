"""Tests for core.component_serialization."""

from __future__ import annotations

import json

import pytest

from core.systems.runtime.component_serialization import (
    AgentSpec,
    TeamSpec,
    ToolSpec,
    WorkflowSpec,
    deserialize_component,
    export_components,
    from_json,
    import_components,
    register_component_type,
    serialize_component,
    to_json,
)


class TestAgentSpec:
    def test_roundtrip(self):
        agent = AgentSpec(name="analyst", role="数据分析师", model="gpt-4", capabilities=["python"])
        d = agent.to_dict()
        restored = AgentSpec.from_dict(d)
        assert restored.name == "analyst"
        assert restored.role == "数据分析师"
        assert restored.model == "gpt-4"
        assert restored.capabilities == ["python"]

    def test_type_marker(self):
        agent = AgentSpec(name="x")
        d = agent.to_dict()
        assert d["_type"] == "agent"

    def test_component_type(self):
        assert AgentSpec(name="x").component_type == "agent"

    def test_defaults(self):
        agent = AgentSpec(name="min")
        assert agent.temperature == 0.7
        assert agent.tools == []
        assert agent.metadata == {}

    def test_extra_fields_ignored(self):
        d = {"name": "a", "role": "r", "unknown_field": 42, "_type": "agent"}
        agent = AgentSpec.from_dict(d)
        assert agent.name == "a"


class TestToolSpec:
    def test_roundtrip(self):
        tool = ToolSpec(name="search", description="搜索工具", cacheable=True, ttl=60.0)
        d = tool.to_dict()
        restored = ToolSpec.from_dict(d)
        assert restored.name == "search"
        assert restored.cacheable is True
        assert restored.ttl == 60.0

    def test_type_marker(self):
        assert ToolSpec(name="x").to_dict()["_type"] == "tool"


class TestTeamSpec:
    def test_roundtrip(self):
        team = TeamSpec(
            name="research",
            agents=[{"name": "a1", "role": "r1"}, {"name": "a2", "role": "r2"}],
            selector_type="llm",
            max_rounds=3,
            mode="society_of_mind",
        )
        d = team.to_dict()
        restored = TeamSpec.from_dict(d)
        assert restored.name == "research"
        assert len(restored.agents) == 2
        assert restored.mode == "society_of_mind"
        assert restored.max_rounds == 3

    def test_defaults(self):
        team = TeamSpec(name="t")
        assert team.selector_type == "round_robin"
        assert team.mode == "team"


class TestWorkflowSpec:
    def test_roundtrip(self):
        wf = WorkflowSpec(
            name="pipeline",
            nodes=[{"id": "n1", "type": "task"}, {"id": "n2", "type": "task"}],
            edges=[{"from": "n1", "to": "n2"}],
            variables={"input": "data"},
        )
        d = wf.to_dict()
        restored = WorkflowSpec.from_dict(d)
        assert restored.name == "pipeline"
        assert len(restored.nodes) == 2
        assert len(restored.edges) == 1
        assert restored.variables == {"input": "data"}


class TestSerializeDeserialize:
    def test_serialize(self):
        agent = AgentSpec(name="a", role="r")
        d = serialize_component(agent)
        assert d["_type"] == "agent"
        assert d["name"] == "a"

    def test_deserialize_agent(self):
        d = {"_type": "agent", "name": "test", "role": "tester"}
        comp = deserialize_component(d)
        assert isinstance(comp, AgentSpec)
        assert comp.name == "test"

    def test_deserialize_tool(self):
        d = {"_type": "tool", "name": "calc"}
        comp = deserialize_component(d)
        assert isinstance(comp, ToolSpec)

    def test_deserialize_team(self):
        d = {"_type": "team", "name": "squad"}
        comp = deserialize_component(d)
        assert isinstance(comp, TeamSpec)

    def test_deserialize_workflow(self):
        d = {"_type": "workflow", "name": "flow"}
        comp = deserialize_component(d)
        assert isinstance(comp, WorkflowSpec)

    def test_missing_type(self):
        with pytest.raises(ValueError, match="_type"):
            deserialize_component({"name": "x"})

    def test_unknown_type(self):
        with pytest.raises(ValueError, match="Unknown"):
            deserialize_component({"_type": "alien", "name": "x"})


class TestJSON:
    def test_to_json(self):
        agent = AgentSpec(name="json_agent", role="coder")
        j = to_json(agent)
        data = json.loads(j)
        assert data["name"] == "json_agent"
        assert data["_type"] == "agent"

    def test_from_json(self):
        j = '{"_type": "agent", "name": "from_json", "role": "test"}'
        comp = from_json(j)
        assert isinstance(comp, AgentSpec)
        assert comp.name == "from_json"

    def test_roundtrip_json(self):
        original = ToolSpec(name="tool1", description="desc", ttl=30.0)
        j = to_json(original)
        restored = from_json(j)
        assert isinstance(restored, ToolSpec)
        assert restored.name == "tool1"
        assert restored.ttl == 30.0


class TestBatchOperations:
    def test_export_import(self):
        components = [
            AgentSpec(name="a1"),
            ToolSpec(name="t1"),
            TeamSpec(name="team1"),
        ]
        exported = export_components(components)
        assert len(exported) == 3
        imported = import_components(exported)
        assert len(imported) == 3
        assert isinstance(imported[0], AgentSpec)
        assert isinstance(imported[1], ToolSpec)
        assert isinstance(imported[2], TeamSpec)

    def test_empty(self):
        assert export_components([]) == []
        assert import_components([]) == []


class TestCustomRegistry:
    def test_register_custom(self):
        from dataclasses import dataclass as dc
        from dataclasses import fields as fs

        @dc
        class CustomSpec:
            name: str
            custom_field: str = ""

            @property
            def component_type(self):
                return "custom"

            def to_dict(self):
                return {"_type": "custom", "name": self.name, "custom_field": self.custom_field}

            @classmethod
            def from_dict(cls, data):
                data = {k: v for k, v in data.items() if k != "_type"}
                valid = {f.name for f in fs(cls)}
                return cls(**{k: v for k, v in data.items() if k in valid})

        register_component_type("custom", CustomSpec)
        d = {"_type": "custom", "name": "test", "custom_field": "value"}
        comp = deserialize_component(d)
        assert isinstance(comp, CustomSpec)
        assert comp.custom_field == "value"
