"""EventBus vocabulary for long-running tasks.

We intentionally do *not* extend :class:`~core.systems.runtime.event_bus.EventType`
with new enum members yet — adding enum members forces every import site to
re-render its switch tables and the registry already covers all five
notifications under one ``EventType`` ("schedule_run").

Instead we publish ``schedule_run`` events with a structured payload whose
``payload["task_event"]`` field is one of the ``TASK_EVENT_TYPES`` constants
below.  Subscribers (web SSE, plugins, dashboards) filter on that field.
"""

from __future__ import annotations

import time
from typing import Any

from core.systems.runtime.event_bus import Event, EventType, event_bus


TASK_EVENT_SPAWNED = "task.spawned"
TASK_EVENT_HEARTBEAT = "task.heartbeat"
TASK_EVENT_STEP = "task.step"
TASK_EVENT_COMPLETED = "task.completed"
TASK_EVENT_FAILED = "task.failed"
TASK_EVENT_CANCELLED = "task.cancelled"

TASK_EVENT_TYPES: tuple[str, ...] = (
    TASK_EVENT_SPAWNED,
    TASK_EVENT_HEARTBEAT,
    TASK_EVENT_STEP,
    TASK_EVENT_COMPLETED,
    TASK_EVENT_FAILED,
    TASK_EVENT_CANCELLED,
)


def emit_task_event(
    *,
    task_event: str,
    task_id: str,
    payload: dict[str, Any] | None = None,
    source: str = "task_registry",
    session_id: str | None = None,
) -> None:
    """Emit a ``schedule_run`` carrying a task-flavoured payload.

    The contract is:

    * ``payload["task_event"]`` — one of :data:`TASK_EVENT_TYPES`.
    * ``payload["task_id"]`` — globally unique task handle.
    * Any other fields are runner-specific (e.g. ``step``, ``progress``).
    """
    if task_event not in TASK_EVENT_TYPES:
        raise ValueError(f"unknown task_event {task_event!r}")
    body: dict[str, Any] = {
        "task_event": task_event,
        "task_id": task_id,
        "ts": time.time(),
    }
    if payload:
        body.update(payload)
    event_bus.emit(
        Event(
            type=EventType.SCHEDULE_RUN,
            payload=body,
            source=source,
            session_id=session_id,
        )
    )


def is_task_event(event: Event, *, task_id: str | None = None) -> bool:
    """Convenience predicate for SSE-side filters."""
    if event.type is not EventType.SCHEDULE_RUN:
        return False
    payload = event.payload or {}
    if "task_event" not in payload:
        return False
    if task_id is not None and payload.get("task_id") != task_id:
        return False
    return True


__all__ = [
    "TASK_EVENT_CANCELLED",
    "TASK_EVENT_COMPLETED",
    "TASK_EVENT_FAILED",
    "TASK_EVENT_HEARTBEAT",
    "TASK_EVENT_SPAWNED",
    "TASK_EVENT_STEP",
    "TASK_EVENT_TYPES",
    "emit_task_event",
    "is_task_event",
]
