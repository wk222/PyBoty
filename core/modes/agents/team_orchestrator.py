"""Multi-agent team orchestration for PyBot.

Provides two process presets for organizing agent collaboration:
- **SequentialTeam**: Tasks execute in order, each agent's output feeds the next.
- **HierarchicalTeam**: A coordinator agent delegates, reviews, and retries sub-tasks.

Both build on top of ``TaskDefinition`` and ``TaskPipeline`` for structured
task management, output validation, and context chaining.

Usage::

    team = SequentialTeam(
        agents={"researcher": agent_def_1, "writer": agent_def_2},
        tasks=[task_research, task_write],
        execute_fn=my_llm_invoke,
    )
    results = team.run()
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Protocol

from core.assets.workflows.task_definition import TaskDefinition, TaskPipeline, TaskStatus

from .agent_storage import AgentDefinition

logger = logging.getLogger(__name__)


class AgentExecutor(Protocol):
    """Protocol for agent execution callables."""

    def __call__(self, prompt: str, agent_name: str | None = None) -> str: ...


@dataclass
class TeamResult:
    """Aggregated result from a team run."""

    task_results: dict[str, Any]
    summary: list[dict[str, Any]]
    success: bool

    @property
    def final_output(self) -> Any:
        if not self.task_results:
            return None
        last_key = list(self.task_results.keys())[-1]
        return self.task_results[last_key]


class SequentialTeam:
    """Execute tasks sequentially, passing context forward.

    Each task runs exactly once in order. If a task has ``context_from``
    defined, the outputs of those tasks are injected into its prompt.
    """

    def __init__(
        self,
        *,
        agents: dict[str, AgentDefinition],
        tasks: list[TaskDefinition],
        execute_fn: AgentExecutor,
    ):
        self._agents = agents
        self._tasks = tasks
        self._execute_fn = execute_fn
        self._pipeline = TaskPipeline(tasks=tasks)

    def run(self, *, stop_on_failure: bool = True) -> TeamResult:
        results = self._pipeline.run(
            self._execute_fn,
            stop_on_failure=stop_on_failure,
        )
        all_done = all(t.status == TaskStatus.COMPLETED for t in self._tasks)
        return TeamResult(
            task_results=results,
            summary=self._pipeline.summary(),
            success=all_done,
        )


class HierarchicalTeam:
    """Coordinator-driven delegation with review and retry.

    The coordinator agent decides task assignment, reviews outputs, and may
    request retries. Combines a planning pass with sequential execution.
    """

    def __init__(
        self,
        *,
        agents: dict[str, AgentDefinition],
        tasks: list[TaskDefinition],
        execute_fn: AgentExecutor,
        coordinator_name: str = "coordinator",
    ):
        self._agents = agents
        self._tasks = {t.name: t for t in tasks}
        self._task_order = [t.name for t in tasks]
        self._execute_fn = execute_fn
        self._coordinator_name = coordinator_name

    def run(self, *, max_reviews: int = 1) -> TeamResult:
        results: dict[str, Any] = {}

        for task_name in self._task_order:
            task = self._tasks[task_name]

            context = {dep: results[dep] for dep in task.context_from if dep in results}
            task.mark_started()
            prompt = task.build_prompt(context=context or None)

            assignment_prompt = self._build_assignment_prompt(task, prompt)
            assignment = self._execute_fn(assignment_prompt, self._coordinator_name)
            assigned_agent = self._parse_assignment(assignment, task)

            try:
                output = self._execute_fn(prompt, assigned_agent)
            except Exception as exc:
                task.mark_failed(str(exc))
                logger.error("Task '%s' failed during execution: %s", task_name, exc)
                continue

            for review_round in range(max_reviews + 1):
                is_valid, validation_error = task.validate_output(output)
                if is_valid:
                    break

                review_prompt = (
                    f"Review the output of task '{task_name}'.\n\n"
                    f"Expected output: {task.expected_output}\n\n"
                    f"Actual output:\n{str(output)[:1500]}\n\n"
                    f"Validation error: {validation_error}\n\n"
                    f"Should this be accepted or retried? "
                    f"If retried, provide specific feedback for the agent."
                )
                review = self._execute_fn(review_prompt, self._coordinator_name)

                if "accept" in review.lower():
                    break

                if review_round < max_reviews:
                    retry_prompt = f"{prompt}\n\n### Coordinator Feedback\n{review}"
                    try:
                        task.mark_started()
                        output = self._execute_fn(retry_prompt, assigned_agent)
                    except Exception as exc:
                        task.mark_failed(str(exc))
                        break

            if task.status != TaskStatus.FAILED:
                task.mark_completed(output)
                results[task_name] = output

        all_done = all(t.status == TaskStatus.COMPLETED for t in self._tasks.values())
        summary = [t.to_dict() for t in self._tasks.values()]
        return TeamResult(task_results=results, summary=summary, success=all_done)

    def _build_assignment_prompt(self, task: TaskDefinition, task_prompt: str) -> str:
        agent_descriptions = []
        for name, agent_def in self._agents.items():
            if name == self._coordinator_name:
                continue
            desc = f"- {name}: {agent_def.role}"
            if agent_def.goal:
                desc += f" (goal: {agent_def.goal})"
            agent_descriptions.append(desc)

        return (
            f"You are the coordinator. Assign the following task to the best agent.\n\n"
            f"Available agents:\n{''.join(agent_descriptions)}\n\n"
            f"Task: {task.name}\n{task.description}\n\n"
            f"Respond with ONLY the agent name (e.g., 'researcher')."
        )

    def _parse_assignment(self, assignment: str, task: TaskDefinition) -> str | None:
        cleaned = assignment.strip().strip("'\"").lower()
        for agent_name in self._agents:
            if agent_name == self._coordinator_name:
                continue
            if agent_name.lower() in cleaned:
                return agent_name
        return task.agent_name
