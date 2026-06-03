"""Snapshot / control adapters for the three concrete runners.

These functions are pure duck-typing wrappers — they never ``import`` the
runner modules so the architectural guard is happy and so the registry
stays L1-pure.  They expect the runner objects to expose the small subset
of attributes the comments below name explicitly.
"""

from __future__ import annotations

import time
from typing import Any

from core.systems.tasks.long_running_task import TaskKind, TaskSnapshot, TaskStatus


# ---------------------------------------------------------------------------
# PersistentAgentRunner / PersistentTask
# Handle shape: (runner, task_id)
# Required PersistentTask attributes:
#   task_id, name, description, status (str enum value), created_at,
#   updated_at, started_at, completed_at, heartbeat_at, progress, error,
#   current_step (object with .description) optional
# ---------------------------------------------------------------------------


_PERSISTENT_STATUS_MAP = {
    "pending": TaskStatus.PENDING,
    "running": TaskStatus.RUNNING,
    "paused": TaskStatus.PAUSED,
    "completed": TaskStatus.COMPLETED,
    "failed": TaskStatus.FAILED,
    "cancelled": TaskStatus.CANCELLED,
}


def _persistent_task(handle: tuple) -> Any:
    runner, task_id = handle
    return runner.get_task(task_id)


def snapshot_persistent(handle: tuple) -> TaskSnapshot:
    task = _persistent_task(handle)
    if task is None:
        return TaskSnapshot(
            task_id=handle[1],
            kind=TaskKind.PERSISTENT,
            name="(deleted)",
            status=TaskStatus.CANCELLED,
            created_at=0.0,
            updated_at=time.time(),
            error="task no longer exists in runner storage",
        )
    raw_status = task.status.value if hasattr(task.status, "value") else str(task.status)
    status = _PERSISTENT_STATUS_MAP.get(raw_status, TaskStatus.RUNNING)
    current = getattr(task, "current_step", None)
    last_step = getattr(current, "description", None) if current is not None else None
    return TaskSnapshot(
        task_id=task.task_id,
        kind=TaskKind.PERSISTENT,
        name=task.name,
        status=status,
        created_at=task.created_at,
        updated_at=task.updated_at,
        started_at=task.started_at,
        completed_at=task.completed_at,
        heartbeat_at=task.heartbeat_at,
        progress=float(task.progress),
        description=task.description,
        error=task.error,
        last_step=last_step,
    )


def cancel_persistent(handle: tuple) -> bool:
    runner, task_id = handle
    return bool(runner.cancel_task(task_id))


def pause_persistent(handle: tuple) -> bool:
    runner, task_id = handle
    return bool(runner.pause_task(task_id))


def resume_persistent(handle: tuple) -> bool:
    runner, task_id = handle
    return bool(runner.resume_task(task_id))


# ---------------------------------------------------------------------------
# MonitorTask
# Handle shape: monitor instance
# Required attributes:
#   config (with .name, .description, .check_interval_seconds), state,
#   _check_count, _last_check_time, _alerts, _last_data
# ---------------------------------------------------------------------------


_MONITOR_STATUS_MAP = {
    "idle": TaskStatus.PENDING,
    "running": TaskStatus.RUNNING,
    "paused": TaskStatus.PAUSED,
    "stopped": TaskStatus.CANCELLED,
    "error": TaskStatus.FAILED,
}


def snapshot_monitor(handle: Any) -> TaskSnapshot:
    monitor = handle
    raw_state = monitor.state.value if hasattr(monitor.state, "value") else str(monitor.state)
    status = _MONITOR_STATUS_MAP.get(raw_state, TaskStatus.RUNNING)
    last_check = getattr(monitor, "_last_check_time", 0.0) or None
    interval = monitor.config.check_interval_seconds
    next_run = (last_check + interval) if last_check else None
    last_alert = None
    alerts = getattr(monitor, "_alerts", [])
    if alerts:
        last_alert = alerts[-1].condition_label
    return TaskSnapshot(
        task_id=f"monitor:{monitor.config.name}",
        kind=TaskKind.MONITOR,
        name=monitor.config.name,
        status=status,
        created_at=last_check or time.time(),
        updated_at=last_check or time.time(),
        started_at=last_check,
        heartbeat_at=last_check,
        next_run_at=next_run,
        progress=0.0,
        description=getattr(monitor.config, "description", "") or "",
        last_step=last_alert,
    )


def cancel_monitor(handle: Any) -> bool:
    handle.stop()
    return True


def pause_monitor(handle: Any) -> bool:
    handle.pause()
    return True


def resume_monitor(handle: Any) -> bool:
    handle.resume()
    return True


# ---------------------------------------------------------------------------
# TaskScheduler / ScheduledTask
# Handle shape: (scheduler, task_name)
# Required ScheduledTask attributes (best-effort):
#   name, cron_expr, enabled, description, created_at, last_run_at, run_count
# ---------------------------------------------------------------------------


def _scheduled(handle: tuple) -> Any | None:
    scheduler, name = handle
    if hasattr(scheduler, "get_task"):
        return scheduler.get_task(name)
    tasks = getattr(scheduler, "_tasks", {})
    return tasks.get(name) if isinstance(tasks, dict) else None


def snapshot_cron(handle: tuple) -> TaskSnapshot:
    scheduled = _scheduled(handle)
    name = handle[1]
    if scheduled is None:
        return TaskSnapshot(
            task_id=f"cron:{name}",
            kind=TaskKind.CRON,
            name=name,
            status=TaskStatus.CANCELLED,
            created_at=0.0,
            updated_at=time.time(),
            error="scheduled task no longer exists",
        )
    enabled = bool(getattr(scheduled, "enabled", True))
    status = TaskStatus.RUNNING if enabled else TaskStatus.PAUSED
    last_run = getattr(scheduled, "last_run_at", None)
    return TaskSnapshot(
        task_id=f"cron:{name}",
        kind=TaskKind.CRON,
        name=name,
        status=status,
        created_at=getattr(scheduled, "created_at", time.time()),
        updated_at=last_run or time.time(),
        started_at=last_run,
        heartbeat_at=last_run,
        next_run_at=None,  # We deliberately don't compute next cron tick here.
        progress=0.0,
        description=getattr(scheduled, "description", "") or "",
        last_step=f"cron={getattr(scheduled, 'cron_expr', '?')} runs={getattr(scheduled, 'run_count', 0)}",
    )


def cancel_cron(handle: tuple) -> bool:
    scheduler, name = handle
    if hasattr(scheduler, "remove_task"):
        return bool(scheduler.remove_task(name))
    if hasattr(scheduler, "delete_task"):
        return bool(scheduler.delete_task(name))
    scheduled = _scheduled(handle)
    if scheduled is not None and hasattr(scheduled, "enabled"):
        scheduled.enabled = False
        return True
    return False


def pause_cron(handle: tuple) -> bool:
    scheduled = _scheduled(handle)
    if scheduled is not None and hasattr(scheduled, "enabled"):
        scheduled.enabled = False
        return True
    return False


def resume_cron(handle: tuple) -> bool:
    scheduled = _scheduled(handle)
    if scheduled is not None and hasattr(scheduled, "enabled"):
        scheduled.enabled = True
        return True
    return False


__all__ = [
    "cancel_cron",
    "cancel_monitor",
    "cancel_persistent",
    "pause_cron",
    "pause_monitor",
    "pause_persistent",
    "resume_cron",
    "resume_monitor",
    "resume_persistent",
    "snapshot_cron",
    "snapshot_monitor",
    "snapshot_persistent",
]
