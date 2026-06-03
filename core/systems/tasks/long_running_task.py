"""LongRunningTask — single normalised representation of any background task.

The three existing runners (PersistentAgentRunner / MonitorTask /
TaskScheduler) keep their full storage and APIs.  This module only provides
a *projection*: the minimum slice of state the web UI, LLM tools and
EventBus need to render and govern any of them uniformly.

A ``LongRunningTask`` is intentionally lightweight: it carries an opaque
``handle`` back to the original runner so callers can still reach the rich,
runner-specific behaviour when needed.
"""

from __future__ import annotations

import time
from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any


class TaskKind(str, Enum):
    """Which underlying runner backs this task."""

    PERSISTENT = "persistent"
    MONITOR = "monitor"
    CRON = "cron"


class TaskStatus(str, Enum):
    """Normalised status, mapped from each runner's native status."""

    PENDING = "pending"
    RUNNING = "running"
    PAUSED = "paused"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass
class TaskSnapshot:
    """A serialisable view of a long-running task at one instant."""

    task_id: str
    kind: TaskKind
    name: str
    status: TaskStatus
    created_at: float
    updated_at: float
    started_at: float | None = None
    completed_at: float | None = None
    heartbeat_at: float | None = None
    next_run_at: float | None = None
    progress: float = 0.0
    parent_thread_id: str | None = None
    description: str = ""
    tags: tuple[str, ...] = ()
    error: str | None = None
    last_step: str | None = None

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["kind"] = self.kind.value
        data["status"] = self.status.value
        data["tags"] = list(self.tags)
        return data


@dataclass
class LongRunningTask:
    """Thin facade pointing at an underlying runner-specific object.

    Attributes
    ----------
    task_id:
        Globally unique handle.  For PERSISTENT this is the runner's
        ``task.task_id``; for MONITOR / CRON we accept the runner's name
        directly so existing storage keys are preserved.
    kind:
        Which runner owns the implementation.
    handle:
        Opaque pointer back to the runner object (e.g. a
        ``PersistentTask`` instance, a ``MonitorTask``, or a
        ``ScheduledTask`` dict).  Callers must not mutate it directly.
    snapshot_fn:
        A pure callable returning a fresh :class:`TaskSnapshot`.  This is
        recomputed on every read so the registry never holds stale state.
    cancel_fn / pause_fn / resume_fn:
        Optional control hooks; ``None`` means the kind does not support
        that operation (e.g. CRON jobs cannot be paused mid-run).
    parent_thread_id:
        The conversation thread that spawned this task, used by the web
        layer to scope SSE streams.
    """

    task_id: str
    kind: TaskKind
    handle: Any
    snapshot_fn: Any
    cancel_fn: Any | None = None
    pause_fn: Any | None = None
    resume_fn: Any | None = None
    parent_thread_id: str | None = None
    spawned_at: float = field(default_factory=time.time)
    extra: dict[str, Any] = field(default_factory=dict)

    def snapshot(self) -> TaskSnapshot:
        snap = self.snapshot_fn(self.handle)
        if not isinstance(snap, TaskSnapshot):
            raise TypeError(
                f"snapshot_fn must return TaskSnapshot, got {type(snap).__name__}"
            )
        if snap.parent_thread_id is None and self.parent_thread_id is not None:
            snap.parent_thread_id = self.parent_thread_id
        return snap

    def cancel(self) -> bool:
        if self.cancel_fn is None:
            return False
        return bool(self.cancel_fn(self.handle))

    def pause(self) -> bool:
        if self.pause_fn is None:
            return False
        return bool(self.pause_fn(self.handle))

    def resume(self) -> bool:
        if self.resume_fn is None:
            return False
        return bool(self.resume_fn(self.handle))


__all__ = [
    "LongRunningTask",
    "TaskKind",
    "TaskSnapshot",
    "TaskStatus",
]
