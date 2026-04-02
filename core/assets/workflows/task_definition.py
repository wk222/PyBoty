"""Task definitions for structured multi-agent collaboration.

Inspired by CrewAI's Task(description, expected_output, agent) pattern,
adapted for PyBot's tool-driven and workflow-based architecture.

A TaskDefinition provides:
- Clear success criteria via ``expected_output`` and optional ``output_schema``
- Explicit agent assignment or auto-routing based on capabilities
- Context chaining between dependent tasks
- Output validation against Pydantic schemas
"""

from __future__ import annotations

import json
import logging
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from pydantic import BaseModel, ValidationError

logger = logging.getLogger(__name__)


class TaskStatus(str, Enum):
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"


@dataclass
class TaskDefinition:
    """A structured task unit for agent collaboration.

    Usage::

        task = TaskDefinition(
            name="research_topic",
            description="Research the latest trends in AI agents",
            expected_output="A 500-word summary with 5 key findings",
            agent_name="researcher",
            output_schema=ResearchResult,
        )
    """

    name: str
    description: str
    expected_output: str
    agent_name: str | None = None
    context_from: list[str] = field(default_factory=list)
    output_schema: type[BaseModel] | None = None
    max_retries: int = 1
    timeout_seconds: float | None = None

    task_id: str = field(default_factory=lambda: f"task_{uuid.uuid4().hex[:12]}")
    status: TaskStatus = TaskStatus.PENDING
    result: Any = None
    error: str | None = None
    started_at: float | None = None
    completed_at: float | None = None
    attempt: int = 0

    def mark_started(self) -> None:
        self.status = TaskStatus.IN_PROGRESS
        self.started_at = time.time()
        self.attempt += 1

    def mark_completed(self, result: Any) -> None:
        self.status = TaskStatus.COMPLETED
        self.result = result
        self.completed_at = time.time()

    def mark_failed(self, error: str) -> None:
        self.status = TaskStatus.FAILED
        self.error = error
        self.completed_at = time.time()

    @property
    def can_retry(self) -> bool:
        return self.attempt < self.max_retries + 1

    @property
    def elapsed_seconds(self) -> float | None:
        if self.started_at is None:
            return None
        end = self.completed_at or time.time()
        return end - self.started_at

    def validate_output(self, output: Any) -> tuple[bool, str]:
        """Validate output against output_schema if defined.

        Returns (is_valid, error_message).
        """
        if self.output_schema is None:
            return True, ""

        if isinstance(output, self.output_schema):
            return True, ""

        if isinstance(output, dict):
            try:
                self.output_schema.model_validate(output)
                return True, ""
            except ValidationError as e:
                return False, str(e)

        if isinstance(output, str):
            try:
                data = json.loads(output)
                self.output_schema.model_validate(data)
                return True, ""
            except (json.JSONDecodeError, ValidationError) as e:
                return False, str(e)

        return False, f"Expected {self.output_schema.__name__}, got {type(output).__name__}"

    def build_prompt(self, context: dict[str, Any] | None = None) -> str:
        """Build a task prompt including context from dependent tasks."""
        parts = [
            f"## Task: {self.name}",
            f"\n{self.description}",
            f"\n### Expected Output\n{self.expected_output}",
        ]
        if self.output_schema is not None:
            try:
                schema_json = json.dumps(
                    self.output_schema.model_json_schema(),
                    indent=2,
                    ensure_ascii=False,
                )
                parts.append(f"\n### Output Schema\n```json\n{schema_json}\n```")
            except Exception:
                pass

        if context:
            parts.append("\n### Context from Previous Tasks")
            for task_name, task_output in context.items():
                output_str = (
                    task_output if isinstance(task_output, str) else json.dumps(task_output, ensure_ascii=False)
                )
                parts.append(f"\n**{task_name}**:\n{output_str[:2000]}")

        return "\n".join(parts)

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "name": self.name,
            "description": self.description,
            "expected_output": self.expected_output,
            "agent_name": self.agent_name,
            "context_from": self.context_from,
            "output_schema": self.output_schema.__name__ if self.output_schema else None,
            "status": self.status.value,
            "result": str(self.result)[:500] if self.result else None,
            "error": self.error,
            "attempt": self.attempt,
            "elapsed_seconds": self.elapsed_seconds,
        }


class TaskPipeline:
    """Execute a sequence of TaskDefinitions, respecting context dependencies.

    Usage::

        pipeline = TaskPipeline(tasks=[task1, task2, task3])
        results = pipeline.run(execute_fn=my_agent_executor)
    """

    def __init__(self, tasks: list[TaskDefinition]):
        self._tasks = {t.name: t for t in tasks}
        self._order = [t.name for t in tasks]

    @property
    def tasks(self) -> list[TaskDefinition]:
        return [self._tasks[name] for name in self._order]

    def run(
        self,
        execute_fn: Any,
        *,
        stop_on_failure: bool = True,
    ) -> dict[str, Any]:
        """Execute tasks in order, passing context between them.

        Args:
            execute_fn: Callable(task_prompt: str, agent_name: str | None) -> str
            stop_on_failure: If True, stop pipeline on first task failure.

        Returns:
            Dict of task_name -> result for completed tasks.
        """
        results: dict[str, Any] = {}

        for name in self._order:
            task = self._tasks[name]

            context = {}
            for dep_name in task.context_from:
                if dep_name in results:
                    context[dep_name] = results[dep_name]

            task.mark_started()
            prompt = task.build_prompt(context=context or None)

            try:
                output = execute_fn(prompt, task.agent_name)
            except Exception as exc:
                task.mark_failed(str(exc))
                logger.error("Task '%s' failed: %s", name, exc)
                if stop_on_failure:
                    break
                continue

            is_valid, validation_error = task.validate_output(output)
            if not is_valid and task.can_retry:
                retry_prompt = (
                    f"{prompt}\n\n### Validation Error\n"
                    f"Your previous output did not match the expected format:\n{validation_error}\n"
                    f"Please try again."
                )
                try:
                    task.mark_started()
                    output = execute_fn(retry_prompt, task.agent_name)
                    is_valid, validation_error = task.validate_output(output)
                except Exception as exc:
                    task.mark_failed(str(exc))
                    if stop_on_failure:
                        break
                    continue

            if not is_valid:
                task.mark_failed(f"Output validation failed: {validation_error}")
                if stop_on_failure:
                    break
                continue

            task.mark_completed(output)
            results[name] = output

        return results

    def summary(self) -> list[dict[str, Any]]:
        return [t.to_dict() for t in self.tasks]
