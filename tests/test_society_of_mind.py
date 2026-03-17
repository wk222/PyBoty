"""Tests for core.society_of_mind."""

from __future__ import annotations

from unittest.mock import MagicMock

from core.event_bus import EventType, event_bus
from core.society_of_mind import MindAgent, SocietyConfig, SocietyOfMind
from core.speaker_selection import RoundRobinSelector


def _mock_llm_factory(response="LLM response"):
    def factory(model="", temperature=0.7):
        llm = MagicMock()
        result = MagicMock()
        result.content = response
        llm.invoke.return_value = result
        return llm

    return factory


def _agents():
    return [
        MindAgent("analyst", role="数据分析师", system_prompt="你擅长数据分析"),
        MindAgent("critic", role="批评者", system_prompt="你擅长找问题"),
    ]


class TestSocietyOfMind:
    def test_basic_run(self):
        soc = SocietyOfMind("team1", _agents(), _mock_llm_factory("Final answer"))
        result = soc.run("Analyze the data")
        assert isinstance(result, str)
        assert len(result) > 0

    def test_custom_config(self):
        config = SocietyConfig(max_rounds=2, synthesizer_model="gpt-4")
        soc = SocietyOfMind("team2", _agents(), _mock_llm_factory("OK"), config=config)
        result = soc.run("Task")
        assert result == "OK"

    def test_conversation_log(self):
        soc = SocietyOfMind("team3", _agents(), _mock_llm_factory("Summary"), config=SocietyConfig(max_rounds=2))
        log = soc.get_conversation_log("Do something")
        assert "task" in log
        assert "conversation" in log
        assert "final_answer" in log
        assert log["final_answer"] == "Summary"
        assert log["agent_count"] == 2
        assert log["message_count"] >= 1

    def test_event_emission(self):
        events = []

        def handler(event):
            events.append(event)

        event_bus.subscribe(EventType.AGENT_START, handler)
        event_bus.subscribe(EventType.AGENT_END, handler)

        soc = SocietyOfMind("ev_team", _agents(), _mock_llm_factory(), config=SocietyConfig(max_rounds=1))
        soc.run("test")

        event_bus.unsubscribe(EventType.AGENT_START, handler)
        event_bus.unsubscribe(EventType.AGENT_END, handler)

        start_events = [e for e in events if e.type == EventType.AGENT_START]
        end_events = [e for e in events if e.type == EventType.AGENT_END]
        assert len(start_events) >= 1
        assert len(end_events) >= 1
        assert start_events[0].payload["mode"] == "society_of_mind"

    def test_with_context(self):
        soc = SocietyOfMind("ctx_team", _agents(), _mock_llm_factory("result"), config=SocietyConfig(max_rounds=1))
        result = soc.run("task", context="important background")
        assert result == "result"

    def test_llm_failure(self):
        def bad_factory(model="", temperature=0.7):
            raise RuntimeError("LLM down")

        agents = _agents()
        soc = SocietyOfMind("fail_team", agents, bad_factory, config=SocietyConfig(max_rounds=1))
        result = soc.run("task")
        assert isinstance(result, str)

    def test_single_agent(self):
        agents = [MindAgent("solo", role="expert")]
        soc = SocietyOfMind("solo_team", agents, _mock_llm_factory("answer"), config=SocietyConfig(max_rounds=1))
        result = soc.run("question")
        assert result == "answer"

    def test_custom_selector(self):
        soc = SocietyOfMind(
            "selector_team",
            _agents(),
            _mock_llm_factory("done"),
            selector=RoundRobinSelector(),
            config=SocietyConfig(max_rounds=3),
        )
        result = soc.run("task")
        assert result == "done"

    def test_discussion_includes_task_each_round(self):
        call_log = []

        def logging_factory(model="", temperature=0.7):
            llm = MagicMock()
            result = MagicMock()
            result.content = "response"

            def invoke_fn(prompt):
                call_log.append(prompt)
                return result

            llm.invoke = invoke_fn
            return llm

        config = SocietyConfig(max_rounds=2, include_task_in_each_round=True)
        soc = SocietyOfMind("log_team", _agents(), logging_factory, config=config)
        soc.run("Specific task XYZ")
        agent_calls = [c for c in call_log if "Specific task XYZ" in c]
        assert len(agent_calls) >= 2  # task mentioned in each round
