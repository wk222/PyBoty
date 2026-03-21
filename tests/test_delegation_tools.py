"""Tests for AskAgentTool and delegation event bus integration."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

from core.agent_creator import AskAgentTool, DelegateToAgentTool, get_agent_creator_tools
from core.agent_storage import AgentDefinition, AgentModelConfig, AgentStorage
from core.event_bus import EventType, event_bus


def _make_agent_def(name="test_agent"):
    return AgentDefinition(
        name=name,
        role="tester",
        description="A test agent",
        system_prompt="You are a test agent.",
        capabilities=["testing"],
        model_config_data=AgentModelConfig(model_id="gpt-4", temperature=0.5),
    )


def _make_storage_with_agent(name="test_agent"):
    storage = AgentStorage()
    storage.add_agent(_make_agent_def(name))
    return storage


class TestAskAgentTool:
    def test_ask_existing_agent(self):
        storage = _make_storage_with_agent()
        mock_llm = MagicMock()
        mock_llm.invoke.return_value = MagicMock(content="42 is the answer")
        llm_factory = MagicMock(return_value=mock_llm)

        tool = AskAgentTool(
            agent_storage=storage,
            llm_factory=llm_factory,
        )
        result = json.loads(tool._run(agent_name="test_agent", question="What is 42?"))
        assert result["success"]
        assert result["answer"] == "42 is the answer"
        llm_factory.assert_called_once_with(model="gpt-4", temperature=0.5)

    def test_ask_with_context(self):
        storage = _make_storage_with_agent()
        mock_llm = MagicMock()
        mock_llm.invoke.return_value = MagicMock(content="Got it")
        llm_factory = MagicMock(return_value=mock_llm)

        tool = AskAgentTool(agent_storage=storage, llm_factory=llm_factory)
        result = json.loads(
            tool._run(
                agent_name="test_agent",
                question="What now?",
                context="Previous analysis showed X",
            )
        )
        assert result["success"]
        prompt_used = mock_llm.invoke.call_args[0][0]
        assert "Previous analysis showed X" in prompt_used

    def test_ask_nonexistent_agent(self):
        storage = _make_storage_with_agent("real_agent")
        tool = AskAgentTool(agent_storage=storage, llm_factory=MagicMock())
        result = json.loads(tool._run(agent_name="ghost", question="Hello?"))
        assert not result["success"]
        assert "不存在" in result["error"]
        assert "real_agent" in result["available_agents"]

    def test_ask_no_storage(self):
        tool = AskAgentTool(agent_storage=None)
        result = json.loads(tool._run(agent_name="x", question="y"))
        assert not result["success"]

    def test_ask_no_llm_factory(self):
        storage = _make_storage_with_agent()
        tool = AskAgentTool(agent_storage=storage, llm_factory=None)
        result = json.loads(tool._run(agent_name="test_agent", question="y"))
        assert not result["success"]

    def test_ask_llm_error(self):
        storage = _make_storage_with_agent()
        mock_llm = MagicMock()
        mock_llm.invoke.side_effect = RuntimeError("LLM boom")
        llm_factory = MagicMock(return_value=mock_llm)

        tool = AskAgentTool(agent_storage=storage, llm_factory=llm_factory)
        result = json.loads(tool._run(agent_name="test_agent", question="?"))
        assert not result["success"]
        assert "boom" in result["error"]


class TestAskAgentEvents:
    def setup_method(self):
        event_bus.clear()

    def test_ask_emits_events(self):
        events = []
        event_bus.subscribe(EventType.AGENT_START, lambda e: events.append(e))
        event_bus.subscribe(EventType.AGENT_END, lambda e: events.append(e))

        storage = _make_storage_with_agent()
        mock_llm = MagicMock()
        mock_llm.invoke.return_value = MagicMock(content="answer")

        tool = AskAgentTool(
            agent_storage=storage,
            llm_factory=MagicMock(return_value=mock_llm),
        )
        tool._run(agent_name="test_agent", question="?")

        assert len(events) == 2
        assert events[0].type == EventType.AGENT_START
        assert events[0].payload["mode"] == "ask"
        assert events[1].type == EventType.AGENT_END
        assert events[1].payload["success"]

    def test_ask_emits_error_event(self):
        events = []
        event_bus.subscribe(EventType.AGENT_END, lambda e: events.append(e))

        storage = _make_storage_with_agent()
        mock_llm = MagicMock()
        mock_llm.invoke.side_effect = RuntimeError("fail")

        tool = AskAgentTool(
            agent_storage=storage,
            llm_factory=MagicMock(return_value=mock_llm),
        )
        tool._run(agent_name="test_agent", question="?")

        assert len(events) == 1
        assert not events[0].payload["success"]


class TestDelegateEmitsEvents:
    def setup_method(self):
        event_bus.clear()

    def test_delegate_emits_start_and_end(self):
        events = []
        event_bus.subscribe(EventType.AGENT_START, lambda e: events.append(e))
        event_bus.subscribe(EventType.AGENT_END, lambda e: events.append(e))

        storage = _make_storage_with_agent()
        tool = DelegateToAgentTool(agent_storage=storage)

        with patch("core.agent_creator.delegate_agent_task") as mock_delegate:
            mock_delegate.return_value = {"success": True, "result": "done"}
            tool._run(agent_name="test_agent", task="do something")

        assert len(events) == 2
        assert events[0].payload["mode"] == "delegate"
        assert events[1].payload["mode"] == "delegate"


class TestGetAgentCreatorToolsIncludeAsk:
    def test_default_includes_ask(self):
        storage = AgentStorage()
        tools = get_agent_creator_tools(storage)
        names = [t.name for t in tools]
        assert "ask_agent" in names

    def test_exclude_ask(self):
        storage = AgentStorage()
        tools = get_agent_creator_tools(storage, include_ask=False)
        names = [t.name for t in tools]
        assert "ask_agent" not in names
