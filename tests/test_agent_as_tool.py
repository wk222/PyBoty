"""Tests for core.agent_as_tool."""

from __future__ import annotations

import json
from unittest.mock import MagicMock

from core.agent_as_tool import AgentTool, TeamTool, create_agent_tool, create_team_tool
from core.event_bus import EventType, event_bus


def _mock_llm_factory(response="LLM response"):
    def factory(model="", temperature=0.7):
        llm = MagicMock()
        result = MagicMock()
        result.content = response
        llm.invoke.return_value = result
        return llm

    return factory


class TestAgentTool:
    def test_basic_call(self):
        tool = AgentTool(
            agent_name="analyst",
            agent_role="data analyst",
            system_prompt="You are a data analyst.",
            llm_factory=_mock_llm_factory("Analysis result: 42"),
        )
        result = tool._run("Analyze this data")
        assert result == "Analysis result: 42"

    def test_tool_name(self):
        tool = AgentTool(agent_name="coder", llm_factory=_mock_llm_factory())
        assert tool.name == "agent_coder"

    def test_with_context(self):
        factory = _mock_llm_factory("OK")
        tool = AgentTool(agent_name="t", system_prompt="sys", llm_factory=factory)
        tool._run("request", context="some background")
        assert tool._run("req", "ctx") == "OK"

    def test_no_llm_factory(self):
        tool = AgentTool(agent_name="t")
        result = tool._run("test")
        data = json.loads(result)
        assert not data["success"]
        assert "llm_factory" in data["error"]

    def test_llm_exception(self):
        def bad_factory(model="", temperature=0.7):
            raise RuntimeError("LLM crashed")

        tool = AgentTool(agent_name="t", llm_factory=bad_factory)
        result = tool._run("test")
        data = json.loads(result)
        assert not data["success"]

    def test_event_emission(self):
        events = []

        def handler(event):
            events.append(event)

        event_bus.subscribe(EventType.AGENT_START, handler)
        event_bus.subscribe(EventType.AGENT_END, handler)

        tool = AgentTool(agent_name="ev_test", llm_factory=_mock_llm_factory())
        tool._run("go")

        event_bus.unsubscribe(EventType.AGENT_START, handler)
        event_bus.unsubscribe(EventType.AGENT_END, handler)

        types = [e.type for e in events]
        assert EventType.AGENT_START in types
        assert EventType.AGENT_END in types

    def test_custom_description(self):
        tool = AgentTool(
            agent_name="x",
            tool_description="Custom tool desc",
            llm_factory=_mock_llm_factory(),
        )
        assert tool.description == "Custom tool desc"


class TestTeamTool:
    def _agents(self):
        return [
            {"name": "Alice", "role": "researcher", "system_prompt": "Research expert"},
            {"name": "Bob", "role": "coder", "system_prompt": "Coding expert"},
        ]

    def test_basic_team(self):
        tool = TeamTool(
            team_name="dream_team",
            agents=self._agents(),
            llm_factory=_mock_llm_factory("Team output"),
            max_rounds=2,
        )
        result = tool._run("Build a feature")
        assert isinstance(result, str)
        assert len(result) > 0

    def test_tool_name(self):
        tool = TeamTool(team_name="alpha", agents=self._agents(), llm_factory=_mock_llm_factory())
        assert tool.name == "team_alpha"

    def test_rounds(self):
        call_count = 0

        def counting_factory(model="", temperature=0.7):
            nonlocal call_count
            call_count += 1
            llm = MagicMock()
            result = MagicMock()
            result.content = f"Response {call_count}"
            llm.invoke.return_value = result
            return llm

        tool = TeamTool(
            team_name="t",
            agents=self._agents(),
            llm_factory=counting_factory,
            max_rounds=3,
        )
        tool._run("task")
        assert call_count == 3

    def test_with_summarizer(self):
        tool = TeamTool(
            team_name="t",
            agents=self._agents(),
            llm_factory=_mock_llm_factory("Summary"),
            max_rounds=2,
            summarizer_prompt="Summarize the conversation",
        )
        result = tool._run("task")
        assert isinstance(result, str)

    def test_no_llm_factory(self):
        tool = TeamTool(team_name="t", agents=self._agents(), max_rounds=1)
        result = tool._run("task")
        assert "Error" in result

    def test_event_emission(self):
        events = []

        def handler(event):
            events.append(event)

        event_bus.subscribe(EventType.AGENT_START, handler)
        event_bus.subscribe(EventType.AGENT_END, handler)

        tool = TeamTool(
            team_name="ev_team",
            agents=self._agents(),
            llm_factory=_mock_llm_factory(),
            max_rounds=1,
        )
        tool._run("go")

        event_bus.unsubscribe(EventType.AGENT_START, handler)
        event_bus.unsubscribe(EventType.AGENT_END, handler)

        assert any(e.type == EventType.AGENT_START for e in events)
        assert any(e.type == EventType.AGENT_END for e in events)

    def test_with_context(self):
        tool = TeamTool(
            team_name="t",
            agents=self._agents(),
            llm_factory=_mock_llm_factory("OK"),
            max_rounds=1,
        )
        result = tool._run("task", context="important context")
        assert isinstance(result, str)


class TestFactoryFunctions:
    def test_create_agent_tool(self):
        tool = create_agent_tool(
            name="helper",
            role="assistant",
            system_prompt="You help with tasks",
            llm_factory=_mock_llm_factory("Done"),
        )
        assert isinstance(tool, AgentTool)
        assert tool.name == "agent_helper"
        assert tool._run("test") == "Done"

    def test_create_team_tool(self):
        agents = [
            {"name": "A", "role": "r1"},
            {"name": "B", "role": "r2"},
        ]
        tool = create_team_tool(
            name="squad",
            agents=agents,
            llm_factory=_mock_llm_factory("Team done"),
            max_rounds=1,
        )
        assert isinstance(tool, TeamTool)
        assert tool.name == "team_squad"
