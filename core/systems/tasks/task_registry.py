"""TaskRegistry — single index over PersistentAgentRunner / MonitorTask /
TaskScheduler.

This is the only object the web layer and LLM tools should hold to manage
background work.  It does **not** schedule anything itself; it only:

* knows which tasks are alive,
* projects them to a uniform :class:`TaskSnapshot`,
* fans control verbs (cancel/pause/resume) back to the right runner,
* emits ``schedule_run`` events with ``task_event`` payload so subscribers
  (SSE, plugins) can render real-time progress.

Adapters for the three concrete runners live in ``adapters.py`` so this
file stays small and testable in isolation.
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable, Iterable
from typing import TYPE_CHECKING, Any

from core.systems.tasks.long_running_task import (
    LongRunningTask,
    TaskKind,
    TaskSnapshot,
    TaskStatus,
)
from core.systems.tasks.task_events import (
    TASK_EVENT_CANCELLED,
    TASK_EVENT_COMPLETED,
    TASK_EVENT_FAILED,
    TASK_EVENT_HEARTBEAT,
    TASK_EVENT_SPAWNED,
    TASK_EVENT_STEP,
    emit_task_event,
)

if TYPE_CHECKING:
    from core.modes.monitor_task import MonitorTask
    from core.systems.agents.persistent_agent_runner import (
        PersistentAgentRunner,
        PersistentTask,
    )

logger = logging.getLogger(__name__)


SnapshotFn = Callable[[Any], TaskSnapshot]
ControlFn = Callable[[Any], bool]


class TaskRegistry:
    """Thread-safe registry of all :class:`LongRunningTask` instances."""

    def __init__(self) -> None:
        self._tasks: dict[str, LongRunningTask] = {}
        self._lock = threading.Lock()

    # ------------------------------------------------------------------
    # Generic registration
    # ------------------------------------------------------------------

    def register(
        self,
        *,
        task_id: str,
        kind: TaskKind,
        handle: Any,
        snapshot_fn: SnapshotFn,
        cancel_fn: ControlFn | None = None,
        pause_fn: ControlFn | None = None,
        resume_fn: ControlFn | None = None,
        parent_thread_id: str | None = None,
        extra: dict[str, Any] | None = None,
    ) -> LongRunningTask:
        task = LongRunningTask(
            task_id=task_id,
            kind=kind,
            handle=handle,
            snapshot_fn=snapshot_fn,
            cancel_fn=cancel_fn,
            pause_fn=pause_fn,
            resume_fn=resume_fn,
            parent_thread_id=parent_thread_id,
            extra=extra or {},
        )
        with self._lock:
            self._tasks[task_id] = task
        snap = task.snapshot()
        emit_task_event(
            task_event=TASK_EVENT_SPAWNED,
            task_id=task_id,
            payload={"snapshot": snap.to_dict()},
            session_id=parent_thread_id,
        )
        return task

    def unregister(self, task_id: str) -> bool:
        with self._lock:
            return self._tasks.pop(task_id, None) is not None

    # ------------------------------------------------------------------
    # Heartbeat / step / completion notifications
    # (called by adapters or directly by runners willing to opt in)
    # ------------------------------------------------------------------

    def touch(
        self,
        task_id: str,
        *,
        step: str | None = None,
        progress: float | None = None,
    ) -> None:
        """Bump heartbeat and optionally announce a step.

        No-op if the task isn't registered (so partially-instrumented
        runners stay safe).
        """
        task = self.get(task_id)
        if task is None:
            return
        snap = task.snapshot()
        if progress is not None:
            snap.progress = progress
        snap.heartbeat_at = time.time()
        if step is not None:
            snap.last_step = step
        payload: dict[str, Any] = {"snapshot": snap.to_dict()}
        emit_task_event(
            task_event=TASK_EVENT_HEARTBEAT,
            task_id=task_id,
            payload=payload,
            session_id=task.parent_thread_id,
        )
        if step is not None:
            emit_task_event(
                task_event=TASK_EVENT_STEP,
                task_id=task_id,
                payload={"step": step, "progress": snap.progress},
                session_id=task.parent_thread_id,
            )

    def complete(self, task_id: str, *, output: Any = None) -> None:
        task = self.get(task_id)
        if task is None:
            return
        emit_task_event(
            task_event=TASK_EVENT_COMPLETED,
            task_id=task_id,
            payload={"snapshot": task.snapshot().to_dict(), "output": output},
            session_id=task.parent_thread_id,
        )

    def fail(self, task_id: str, *, error: str) -> None:
        task = self.get(task_id)
        if task is None:
            return
        emit_task_event(
            task_event=TASK_EVENT_FAILED,
            task_id=task_id,
            payload={"snapshot": task.snapshot().to_dict(), "error": error},
            session_id=task.parent_thread_id,
        )

    # ------------------------------------------------------------------
    # Reads
    # ------------------------------------------------------------------

    def get(self, task_id: str) -> LongRunningTask | None:
        with self._lock:
            return self._tasks.get(task_id)

    def list(
        self,
        *,
        kind: TaskKind | None = None,
        status: TaskStatus | None = None,
        thread_id: str | None = None,
    ) -> list[TaskSnapshot]:
        with self._lock:
            tasks = list(self._tasks.values())
        out: list[TaskSnapshot] = []
        for task in tasks:
            try:
                snap = task.snapshot()
            except Exception:
                logger.exception("snapshot failed for task %s", task.task_id)
                continue
            if kind is not None and snap.kind != kind:
                continue
            if status is not None and snap.status != status:
                continue
            if thread_id is not None and snap.parent_thread_id != thread_id:
                continue
            out.append(snap)
        out.sort(key=lambda s: s.updated_at, reverse=True)
        return out

    def __len__(self) -> int:
        with self._lock:
            return len(self._tasks)

    def __iter__(self) -> Iterable[LongRunningTask]:
        with self._lock:
            return iter(list(self._tasks.values()))

    # ------------------------------------------------------------------
    # Control verbs (delegate to adapter)
    # ------------------------------------------------------------------

    def cancel(self, task_id: str) -> bool:
        task = self.get(task_id)
        if task is None:
            return False
        ok = task.cancel()
        if ok:
            emit_task_event(
                task_event=TASK_EVENT_CANCELLED,
                task_id=task_id,
                payload={"snapshot": task.snapshot().to_dict()},
                session_id=task.parent_thread_id,
            )
        return ok

    def pause(self, task_id: str) -> bool:
        task = self.get(task_id)
        return False if task is None else task.pause()

    def resume(self, task_id: str) -> bool:
        task = self.get(task_id)
        return False if task is None else task.resume()

    def clear(self) -> None:
        """Test helper — drop all registered tasks (no events emitted)."""
        with self._lock:
            self._tasks.clear()

    # ------------------------------------------------------------------
    # Concrete adapters
    # ------------------------------------------------------------------

    def attach_persistent(
        self,
        runner: "PersistentAgentRunner",
        task: "PersistentTask",
        *,
        parent_thread_id: str | None = None,
    ) -> LongRunningTask:
        """Register a :class:`PersistentTask` under its own ``task_id``."""
        from core.systems.tasks.adapters import (
            cancel_persistent,
            pause_persistent,
            resume_persistent,
            snapshot_persistent,
        )

        return self.register(
            task_id=task.task_id,
            kind=TaskKind.PERSISTENT,
            handle=(runner, task.task_id),
            snapshot_fn=snapshot_persistent,
            cancel_fn=cancel_persistent,
            pause_fn=pause_persistent,
            resume_fn=resume_persistent,
            parent_thread_id=parent_thread_id,
        )

    def attach_monitor(
        self,
        monitor: "MonitorTask",
        *,
        parent_thread_id: str | None = None,
    ) -> LongRunningTask:
        from core.systems.tasks.adapters import (
            cancel_monitor,
            pause_monitor,
            resume_monitor,
            snapshot_monitor,
        )

        return self.register(
            task_id=f"monitor:{monitor.config.name}",
            kind=TaskKind.MONITOR,
            handle=monitor,
            snapshot_fn=snapshot_monitor,
            cancel_fn=cancel_monitor,
            pause_fn=pause_monitor,
            resume_fn=resume_monitor,
            parent_thread_id=parent_thread_id,
        )

    def attach_cron(
        self,
        scheduler: Any,
        scheduled_task: Any,
        *,
        parent_thread_id: str | None = None,
    ) -> LongRunningTask:
        """Register an entry from :class:`TaskScheduler`.

        ``scheduled_task`` must expose ``.name``, ``.cron_expr`` and
        ``.enabled`` attributes (matching ``ScheduledTask``).
        """
        from core.systems.tasks.adapters import (
            cancel_cron,
            pause_cron,
            resume_cron,
            snapshot_cron,
        )

        return self.register(
            task_id=f"cron:{scheduled_task.name}",
            kind=TaskKind.CRON,
            handle=(scheduler, scheduled_task.name),
            snapshot_fn=snapshot_cron,
            cancel_fn=cancel_cron,
            pause_fn=pause_cron,
            resume_fn=resume_cron,
            parent_thread_id=parent_thread_id,
        )


task_registry = TaskRegistry()


__all__ = ["TaskRegistry", "task_registry"]
