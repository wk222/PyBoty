"""Tests for core.team_orchestrator — SequentialTeam and HierarchicalTeam."""

from __future__ import annotations

from core.agent_storage import AgentDefinition
from core.task_definition import TaskDefinition
from core.team_orchestrator import HierarchicalTeam, SequentialTeam, TeamResult


def _make_agent(name: str, role: str, goal: str = "") -> AgentDefinition:
    return AgentDefinition(
        name=name,
        role=role,
        description=f"Agent: {name}",
        system_prompt=f"You are {name}.",
        goal=goal,
    )


class TestSequentialTeam:
    def test_basic_sequential(self):
        agents = {
            "researcher": _make_agent("researcher", "Researches topics"),
            "writer": _make_agent("writer", "Writes content"),
        }
        tasks = [
            TaskDefinition(name="research", description="Research AI", expected_output="findings", agent_name="researcher"),
            TaskDefinition(name="write", description="Write article", expected_output="article", agent_name="writer", context_from=["research"]),
        ]

        def execute_fn(prompt, agent_name=None):
            if agent_name == "researcher":
                return "AI is transforming everything"
            return "Article about AI transformation"

        team = SequentialTeam(agents=agents, tasks=tasks, execute_fn=execute_fn)
        result = team.run()
        assert result.success is True
        assert "research" in result.task_results
        assert "write" in result.task_results
        assert result.final_output == "Article about AI transformation"
        assert len(result.summary) == 2

    def test_failure_stops_pipeline(self):
        agents = {"a": _make_agent("a", "Agent A")}
        tasks = [
            TaskDefinition(name="t1", description="fail", expected_output="x"),
            TaskDefinition(name="t2", description="skip", expected_output="y"),
        ]

        def execute_fn(prompt, agent_name=None):
            raise RuntimeError("boom")

        team = SequentialTeam(agents=agents, tasks=tasks, execute_fn=execute_fn)
        result = team.run(stop_on_failure=True)
        assert result.success is False
        assert len(result.task_results) == 0


class TestHierarchicalTeam:
    def test_coordinator_delegates_and_reviews(self):
        agents = {
            "coordinator": _make_agent("coordinator", "Coordinator", goal="Coordinate team"),
            "researcher": _make_agent("researcher", "Researcher"),
        }
        tasks = [
            TaskDefinition(name="research", description="Research topic", expected_output="findings", agent_name="researcher"),
        ]
        call_log = []

        def execute_fn(prompt, agent_name=None):
            call_log.append(agent_name)
            if agent_name == "coordinator":
                return "researcher"
            return "Research findings here"

        team = HierarchicalTeam(agents=agents, tasks=tasks, execute_fn=execute_fn, coordinator_name="coordinator")
        result = team.run()
        assert result.success is True
        assert "coordinator" in call_log
        assert "researcher" in call_log

    def test_coordinator_retry_on_validation_failure(self):
        from pydantic import BaseModel, Field

        class Output(BaseModel):
            data: str = Field(description="data")

        agents = {
            "coordinator": _make_agent("coordinator", "Coordinator"),
            "worker": _make_agent("worker", "Worker"),
        }
        tasks = [
            TaskDefinition(
                name="task1", description="Do work",
                expected_output="JSON", agent_name="worker",
                output_schema=Output, max_retries=1,
            ),
        ]
        call_count = {"worker": 0, "coordinator": 0}

        def execute_fn(prompt, agent_name=None):
            if agent_name == "coordinator":
                call_count["coordinator"] += 1
                if "assign" in prompt.lower():
                    return "worker"
                return "retry"
            call_count["worker"] += 1
            if call_count["worker"] == 1:
                return "bad output"
            return '{"data": "good"}'

        team = HierarchicalTeam(agents=agents, tasks=tasks, execute_fn=execute_fn, coordinator_name="coordinator")
        result = team.run(max_reviews=1)
        assert result.success is True
        assert call_count["worker"] >= 2


class TestTeamResult:
    def test_final_output(self):
        result = TeamResult(
            task_results={"t1": "first", "t2": "second"},
            summary=[],
            success=True,
        )
        assert result.final_output == "second"

    def test_empty_final_output(self):
        result = TeamResult(task_results={}, summary=[], success=False)
        assert result.final_output is None
