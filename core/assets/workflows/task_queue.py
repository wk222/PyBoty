"""Lightweight async task queue for background operations.

No heavy dependencies (no Celery/Redis). Uses Python's built-in
concurrent.futures ThreadPoolExecutor + asyncio for async support.

Use cases:
  - Long-running Workflow execution
  - Document ingestion (RAG)
  - Channel webhook processing
  - Batch tool invocations
"""

from __future__ import annotations

import logging
import threading
import time
import uuid
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

logger = logging.getLogger(__name__)


class TaskStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass
class TaskInfo:
    """Metadata about a queued task."""

    task_id: str
    name: str
    status: TaskStatus
    created_at: float
    started_at: float | None = None
    completed_at: float | None = None
    result: Any = None
    error: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class TaskHandle:
    """Returned when a task is submitted."""

    task_id: str
    name: str
    status: TaskStatus


class TaskQueue:
    """Thread-pool-backed task queue with status tracking."""

    def __init__(self, max_workers: int = 4, max_history: int = 200):
        self._executor = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="task_queue")
        self._tasks: dict[str, TaskInfo] = {}
        self._futures: dict[str, Future] = {}
        self._lock = threading.Lock()
        self._max_history = max_history

    def submit(
        self,
        task_fn: Callable[..., Any],
        *args: Any,
        name: str = "",
        metadata: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> TaskHandle:
        """Submit a task for background execution."""
        task_id = str(uuid.uuid4())[:12]
        task_name = name or getattr(task_fn, "__name__", "unnamed")

        info = TaskInfo(
            task_id=task_id,
            name=task_name,
            status=TaskStatus.PENDING,
            created_at=time.time(),
            metadata=metadata or {},
        )

        with self._lock:
            self._tasks[task_id] = info
            self._prune_history()

        future = self._executor.submit(self._run_task, task_id, task_fn, args, kwargs)
        with self._lock:
            self._futures[task_id] = future

        logger.info("Task submitted: %s (%s)", task_name, task_id)
        return TaskHandle(task_id=task_id, name=task_name, status=TaskStatus.PENDING)

    def _run_task(
        self,
        task_id: str,
        task_fn: Callable,
        args: tuple,
        kwargs: dict,
    ) -> Any:
        with self._lock:
            info = self._tasks.get(task_id)
            if info:
                info.status = TaskStatus.RUNNING
                info.started_at = time.time()

        try:
            result = task_fn(*args, **kwargs)
            with self._lock:
                info = self._tasks.get(task_id)
                if info:
                    info.status = TaskStatus.COMPLETED
                    info.result = result
                    info.completed_at = time.time()
            return result
        except Exception as exc:
            with self._lock:
                info = self._tasks.get(task_id)
                if info:
                    info.status = TaskStatus.FAILED
                    info.error = str(exc)
                    info.completed_at = time.time()
            logger.error("Task %s failed: %s", task_id, exc)
            raise

    def get_status(self, task_id: str) -> TaskInfo | None:
        """Get current task info."""
        with self._lock:
            return self._tasks.get(task_id)

    def cancel(self, task_id: str) -> bool:
        """Attempt to cancel a pending/running task."""
        with self._lock:
            future = self._futures.get(task_id)
            info = self._tasks.get(task_id)

        if future is None or info is None:
            return False

        if info.status in (TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.CANCELLED):
            return False

        cancelled = future.cancel()
        if cancelled:
            with self._lock:
                info.status = TaskStatus.CANCELLED
                info.completed_at = time.time()
            logger.info("Task %s cancelled", task_id)
        return cancelled

    def list_active(self) -> list[TaskInfo]:
        """List all pending and running tasks."""
        with self._lock:
            return [info for info in self._tasks.values() if info.status in (TaskStatus.PENDING, TaskStatus.RUNNING)]

    def list_all(self) -> list[TaskInfo]:
        """List all tracked tasks."""
        with self._lock:
            return list(self._tasks.values())

    def wait(self, task_id: str, timeout: float | None = None) -> TaskInfo | None:
        """Block until a task completes (or timeout)."""
        future = self._futures.get(task_id)
        if future is None:
            return self.get_status(task_id)

        try:
            future.result(timeout=timeout)
        except Exception:
            pass
        return self.get_status(task_id)

    def get_summary(self) -> dict[str, int]:
        """Quick summary of task counts by status."""
        with self._lock:
            counts: dict[str, int] = {}
            for info in self._tasks.values():
                counts[info.status.value] = counts.get(info.status.value, 0) + 1
            return counts

    def shutdown(self, wait: bool = True) -> None:
        """Shutdown the executor."""
        self._executor.shutdown(wait=wait)

    def _prune_history(self) -> None:
        """Remove oldest completed/failed tasks if over limit."""
        if len(self._tasks) <= self._max_history:
            return

        finished = sorted(
            [
                (tid, info)
                for tid, info in self._tasks.items()
                if info.status in (TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.CANCELLED)
            ],
            key=lambda x: x[1].completed_at or 0,
        )

        to_remove = len(self._tasks) - self._max_history
        for tid, _ in finished[:to_remove]:
            del self._tasks[tid]
            self._futures.pop(tid, None)

    def prune_stale_tasks(self, timeout_sec: float = 3600) -> int:
        """Mark tasks stuck in RUNNING for too long as FAILED (Reaper hook)."""
        now = time.time()
        stale_count = 0
        with self._lock:
            for task in self._tasks.values():
                if task.status == TaskStatus.RUNNING and task.started_at:
                    if now - task.started_at > timeout_sec:
                        task.status = TaskStatus.FAILED
                        task.error = "Task timed out (reaped by daemon)"
                        task.completed_at = now
                        stale_count += 1
                        logger.warning("Reaped stale task: %s (%s)", task.name, task.task_id)
        return stale_count


    def submit_batch(
        self,
        batch_fn: Callable[..., dict[str, Any]],
        *,
        name: str = "",
        batch_size: int = 100,
        max_batches: int = 0,
        initial_cursor: str | None = None,
        delay_between: float = 0.0,
        metadata: dict[str, Any] | None = None,
    ) -> TaskHandle:
        """Submit a self-scheduling batch job.

        The batch_fn must accept (cursor: str|None, batch_size: int) and
        return a dict with keys: cursor, processed, done.

        The queue auto-schedules continuation batches until done or
        max_batches is reached.
        """

        def _run_batches() -> dict[str, Any]:
            cursor = initial_cursor
            total_processed = 0
            batch_count = 0

            while True:
                if 0 < max_batches <= batch_count:
                    return {
                        "status": "paused",
                        "cursor": cursor,
                        "total_processed": total_processed,
                        "batch_count": batch_count,
                    }

                result = batch_fn(cursor, batch_size)
                cursor = result.get("cursor")
                processed = result.get("processed", 0)
                done = result.get("done", False)

                total_processed += processed
                batch_count += 1

                if done or processed == 0:
                    return {
                        "status": "completed",
                        "cursor": cursor,
                        "total_processed": total_processed,
                        "batch_count": batch_count,
                    }

                if delay_between > 0:
                    time.sleep(delay_between)

        return self.submit(
            _run_batches,
            name=name or "batch_job",
            metadata={**(metadata or {}), "batch_size": batch_size},
        )


class CheckpointerFactory:
    """Factory for creating LangGraph checkpoint savers."""

    @staticmethod
    def create(config: dict[str, Any] | None = None):
        """Create a checkpointer from config.

        Config keys:
          - type: "sqlite" (default) or "postgres"
          - path: SQLite database path (for sqlite type)
          - connection_string: PostgreSQL connection string (for postgres type)
        """
        import sqlite3

        from langgraph.checkpoint.sqlite import SqliteSaver

        if config is None:
            config = {}

        db_type = config.get("type", "sqlite")

        if db_type == "postgres":
            try:
                from langgraph.checkpoint.postgres import PostgresSaver

                conn_str = config.get("connection_string", "")
                if not conn_str:
                    raise ValueError("PostgreSQL requires 'connection_string' in config")
                return PostgresSaver(conn_str)
            except ImportError:
                logger.warning("langgraph-checkpoint-postgres not installed, falling back to SQLite")
                db_type = "sqlite"

        db_path = config.get("path", "workspace/checkpoints.db")
        conn = sqlite3.connect(db_path, check_same_thread=False)
        return SqliteSaver(conn)
