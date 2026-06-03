"""Persistent Agent Runner — Manus-style durable agent execution.

Allows agents to run complex, multi-step tasks that survive process restarts.
Key features:
- Task state persisted to disk after each step
- Resumable from last checkpoint
- Progress tracking with intermediate results
- Timeout and heartbeat support
- Background execution via TaskQueue
"""

from __future__ import annotations

import json
import logging
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


class PersistentTaskStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    PAUSED = "paused"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass
class PersistentTaskStep:
    """A single step in a persistent task execution."""

    step_id: str
    description: str
    status: str = "pending"
    input_data: Any = None
    output_data: Any = None
    started_at: float | None = None
    completed_at: float | None = None
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "step_id": self.step_id,
            "description": self.description,
            "status": self.status,
            "input_data": self.input_data,
            "output_data": self.output_data,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "error": self.error,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> PersistentTaskStep:
        return cls(
            step_id=d["step_id"],
            description=d.get("description", ""),
            status=d.get("status", "pending"),
            input_data=d.get("input_data"),
            output_data=d.get("output_data"),
            started_at=d.get("started_at"),
            completed_at=d.get("completed_at"),
            error=d.get("error"),
        )


@dataclass
class PersistentTask:
    """A durable, multi-step task that persists across restarts."""

    task_id: str
    name: str
    description: str
    agent_name: str
    status: PersistentTaskStatus = PersistentTaskStatus.PENDING
    steps: list[PersistentTaskStep] = field(default_factory=list)
    context: dict[str, Any] = field(default_factory=dict)
    final_output: Any = None
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    started_at: float | None = None
    completed_at: float | None = None
    error: str | None = None
    heartbeat_at: float | None = None
    max_steps: int = 50
    timeout_seconds: float | None = None

    @property
    def progress(self) -> float:
        if not self.steps:
            return 0.0
        completed = sum(1 for s in self.steps if s.status == "completed")
        return completed / len(self.steps)

    @property
    def current_step(self) -> PersistentTaskStep | None:
        for step in self.steps:
            if step.status in ("pending", "running"):
                return step
        return None

    def add_step(self, description: str, input_data: Any = None) -> PersistentTaskStep:
        step = PersistentTaskStep(
            step_id=f"step_{len(self.steps) + 1}",
            description=description,
            input_data=input_data,
        )
        self.steps.append(step)
        return step

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "name": self.name,
            "description": self.description,
            "agent_name": self.agent_name,
            "status": self.status.value,
            "steps": [s.to_dict() for s in self.steps],
            "context": self.context,
            "final_output": self.final_output,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "error": self.error,
            "heartbeat_at": self.heartbeat_at,
            "max_steps": self.max_steps,
            "timeout_seconds": self.timeout_seconds,
            "progress": self.progress,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> PersistentTask:
        task = cls(
            task_id=d["task_id"],
            name=d["name"],
            description=d.get("description", ""),
            agent_name=d.get("agent_name", ""),
            status=PersistentTaskStatus(d.get("status", "pending")),
            context=d.get("context", {}),
            final_output=d.get("final_output"),
            created_at=d.get("created_at", 0),
            updated_at=d.get("updated_at", 0),
            started_at=d.get("started_at"),
            completed_at=d.get("completed_at"),
            error=d.get("error"),
            heartbeat_at=d.get("heartbeat_at"),
            max_steps=d.get("max_steps", 50),
            timeout_seconds=d.get("timeout_seconds"),
        )
        for sd in d.get("steps", []):
            task.steps.append(PersistentTaskStep.from_dict(sd))
        return task


class PersistentAgentRunner:
    """Manages durable agent task execution with disk persistence."""

    def __init__(self, storage_dir: str):
        self._storage_dir = Path(storage_dir) / "persistent_tasks"
        self._storage_dir.mkdir(parents=True, exist_ok=True)
        self._tasks: dict[str, PersistentTask] = {}
        self._load_tasks()

    def _load_tasks(self) -> None:
        for fp in self._storage_dir.glob("*.json"):
            try:
                data = json.loads(fp.read_text(encoding="utf-8"))
                task = PersistentTask.from_dict(data)
                self._tasks[task.task_id] = task
            except Exception:
                logger.debug("Failed to load persistent task: %s", fp.name)

    def _save_task(self, task: PersistentTask) -> None:
        task.updated_at = time.time()
        fp = self._storage_dir / f"{task.task_id}.json"
        fp.write_text(
            json.dumps(task.to_dict(), ensure_ascii=False, indent=2, default=str),
            encoding="utf-8",
        )

    @staticmethod
    def _rebuild_pending_steps(
        task: PersistentTask,
        steps: list[str],
    ) -> None:
        completed_steps = [step for step in task.steps if step.status == "completed"]
        pending_descriptions = [str(step).strip() for step in steps if str(step).strip()]
        task.steps = completed_steps
        for description in pending_descriptions:
            task.add_step(description)

    def create_task(
        self,
        *,
        name: str,
        description: str,
        agent_name: str,
        steps: list[str] | None = None,
        context: dict[str, Any] | None = None,
        max_steps: int = 50,
        timeout_seconds: float | None = None,
    ) -> PersistentTask:
        task = PersistentTask(
            task_id=uuid.uuid4().hex[:12],
            name=name,
            description=description,
            agent_name=agent_name,
            context=context or {},
            max_steps=max_steps,
            timeout_seconds=timeout_seconds,
        )
        if steps:
            for desc in steps:
                task.add_step(desc)
        self._tasks[task.task_id] = task
        self._save_task(task)
        return task

    def get_task(self, task_id: str) -> PersistentTask | None:
        return self._tasks.get(task_id)

    def merge_context(self, task_id: str, values: dict[str, Any]) -> bool:
        task = self._tasks.get(task_id)
        if task is None:
            return False
        task.context.update(values)
        self._save_task(task)
        return True

    def replace_pending_steps(self, task_id: str, steps: list[str]) -> bool:
        task = self._tasks.get(task_id)
        if task is None:
            return False
        self._rebuild_pending_steps(task, steps)
        if task.steps and task.status == PersistentTaskStatus.COMPLETED:
            task.status = PersistentTaskStatus.RUNNING
            task.completed_at = None
            task.final_output = None
        self._save_task(task)
        return True

    def list_tasks(self, status: PersistentTaskStatus | None = None) -> list[PersistentTask]:
        tasks = list(self._tasks.values())
        if status:
            tasks = [t for t in tasks if t.status == status]
        return sorted(tasks, key=lambda t: t.updated_at, reverse=True)

    def execute_step(
        self,
        task_id: str,
        step_fn: Any,
    ) -> PersistentTaskStep | None:
        """Execute the next pending step.  Returns the step or None if done."""
        task = self._tasks.get(task_id)
        if task is None or task.status not in (PersistentTaskStatus.PENDING, PersistentTaskStatus.RUNNING):
            return None

        step = task.current_step
        if step is None:
            task.status = PersistentTaskStatus.COMPLETED
            task.completed_at = time.time()
            self._save_task(task)
            return None

        task.status = PersistentTaskStatus.RUNNING
        task.heartbeat_at = time.time()
        if task.started_at is None:
            task.started_at = time.time()

        step.status = "running"
        step.started_at = time.time()
        self._save_task(task)

        try:
            result = step_fn(step, task.context)
            context_update: dict[str, Any] | None = None
            final_output = result
            if isinstance(result, dict) and ("__step_output__" in result or "__context_update__" in result):
                step.output_data = result.get("__step_output__")
                raw_context_update = result.get("__context_update__", {})
                context_update = raw_context_update if isinstance(raw_context_update, dict) else {}
                final_output = step.output_data
            else:
                step.output_data = result

            waiting_approval = self._extract_waiting_approval(step.output_data)
            if waiting_approval is not None:
                step.status = "paused"
                task.status = PersistentTaskStatus.PAUSED
                task.context["pending_approval"] = {
                    "approval_id": waiting_approval["approval_id"],
                    "step_id": step.step_id,
                    "step_description": step.description,
                    "response": waiting_approval.get("response", ""),
                    "created_at": time.time(),
                }
                task.context["last_step_waiting_approval"] = waiting_approval["approval_id"]
                task.heartbeat_at = time.time()
                self._save_task(task)
                return step

            step.status = "completed"
            step.completed_at = time.time()

            if context_update is not None:
                task.context.update(context_update)
            elif isinstance(result, dict):
                task.context.update(result)

            if task.current_step is None:
                task.status = PersistentTaskStatus.COMPLETED
                task.completed_at = time.time()
                task.final_output = final_output

        except Exception as exc:
            step.status = "failed"
            step.error = str(exc)
            step.completed_at = time.time()
            task.status = PersistentTaskStatus.FAILED
            task.error = str(exc)

        task.heartbeat_at = time.time()
        self._save_task(task)
        return step

    @staticmethod
    def _extract_waiting_approval(output: Any) -> dict[str, Any] | None:
        if not isinstance(output, dict):
            return None
        approval_id = str(output.get("approval_id", "")).strip()
        status = str(output.get("status", "")).strip()
        if status != "waiting_approval" or not approval_id:
            return None
        return output

    def run_all_steps(self, task_id: str, step_fn: Any) -> PersistentTask | None:
        """Run all pending steps sequentially."""
        task = self._tasks.get(task_id)
        if task is None:
            return None

        while task.status in (PersistentTaskStatus.PENDING, PersistentTaskStatus.RUNNING):
            if task.timeout_seconds and task.started_at:
                if time.time() - task.started_at > task.timeout_seconds:
                    task.status = PersistentTaskStatus.FAILED
                    task.error = f"Timeout after {task.timeout_seconds}s"
                    self._save_task(task)
                    break

            step = self.execute_step(task_id, step_fn)
            if step is None:
                break
            if step.status == "failed":
                break

        return task

    def pause_task(self, task_id: str) -> bool:
        task = self._tasks.get(task_id)
        if task is None or task.status != PersistentTaskStatus.RUNNING:
            return False
        task.status = PersistentTaskStatus.PAUSED
        self._save_task(task)
        return True

    def resume_task(self, task_id: str) -> bool:
        task = self._tasks.get(task_id)
        if task is None or task.status != PersistentTaskStatus.PAUSED:
            return False
        task.status = PersistentTaskStatus.RUNNING
        self._save_task(task)
        return True

    def cancel_task(self, task_id: str) -> bool:
        task = self._tasks.get(task_id)
        if task is None or task.status in (PersistentTaskStatus.COMPLETED, PersistentTaskStatus.CANCELLED):
            return False
        task.status = PersistentTaskStatus.CANCELLED
        task.completed_at = time.time()
        self._save_task(task)
        return True

    def get_resumable_tasks(self) -> list[PersistentTask]:
        """Find tasks that were running/paused and can be resumed."""
        return [
            t for t in self._tasks.values() if t.status in (PersistentTaskStatus.RUNNING, PersistentTaskStatus.PAUSED)
        ]
