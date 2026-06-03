"""Long-running task subsystem — Layer 1.

Unifies the three existing background runners into a single observable surface:

* :class:`~core.systems.agents.persistent_agent_runner.PersistentAgentRunner`
  — durable multi-step agent tasks (Codex-style "keep working after I close
  the tab").
* :class:`~core.modes.monitor_task.MonitorTask`
  — condition-triggered watchers with cooldown and baselines (Codex-style
  "alert me when X").
* :class:`~core.assets.workflows.task_scheduler.TaskScheduler`
  — cron-driven recurring jobs (Codex-style "every 30 min run this").

The unification is intentionally thin: the original runners keep their full
APIs and storage, the registry only adds (a) a global handle table, (b) a
common event vocabulary on the EventBus, and (c) a uniform query / cancel /
pause / resume facade so the web layer and LLM tools have a single endpoint
to talk to.
"""

from __future__ import annotations

# Eagerly export the singleton because Python's ``from pkg import name``
# resolution prefers a same-named submodule (``task_registry.py``) over a
# lazy ``__getattr__`` attribute, which would otherwise hand callers the
# module object instead of the instance.
from core.systems.tasks.long_running_task import (
    LongRunningTask,
    TaskKind,
    TaskSnapshot,
    TaskStatus,
)
from core.systems.tasks.task_events import (
    TASK_EVENT_TYPES,
    emit_task_event,
    is_task_event,
)
from core.systems.tasks.task_registry import TaskRegistry, task_registry
from core.systems.tasks.scheduling_tools import build_scheduling_tools

__all__ = [
    "LongRunningTask",
    "TASK_EVENT_TYPES",
    "TaskKind",
    "TaskRegistry",
    "TaskSnapshot",
    "TaskStatus",
    "build_scheduling_tools",
    "emit_task_event",
    "is_task_event",
    "task_registry",
]


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(__all__))
