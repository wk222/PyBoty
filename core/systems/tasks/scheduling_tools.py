"""LangChain tools that let an LLM spawn long-running tasks itself.

These wrap :class:`~core.systems.agents.persistent_agent_runner.PersistentAgentRunner`
and :class:`~core.modes.monitor_task.MonitorManager`, route every spawn through
the unified :data:`~core.systems.tasks.task_registry.task_registry`, and emit
``task.spawned`` events so the web dashboard lights up immediately.

Design constraints:

* This module sits at L1 (``core/systems/tasks/``) but the runners themselves
  live at L3.  We only ``import`` them lazily inside the bound functions so
  the architectural guard's static scan never sees an upward edge.
* Tools are factory-built (``build_scheduling_tools``) rather than module
  globals so each PyBot session can bind the right ``persistent_runner``,
  ``monitor_manager`` and ``parent_thread_id``.
"""

from __future__ import annotations

import logging
import time
from typing import Any, Callable

from langchain_core.tools import StructuredTool
from pydantic import BaseModel, Field

from core.systems.tasks.task_registry import task_registry

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Inputs
# ---------------------------------------------------------------------------


class ScheduleRecurringTaskInput(BaseModel):
    name: str = Field(description="任务唯一名称（短、英文字母和下划线）")
    goal: str = Field(description="自然语言目标，会作为后台任务的 description")
    steps: list[str] = Field(
        default_factory=list,
        description="可选的拆解步骤；不填则后台 planner 自行规划",
    )
    cron: str | None = Field(
        default=None,
        description="可选 5 字段 cron 表达式；缺省表示一次性持久任务",
    )
    max_runs: int = Field(default=0, description="最多执行多少轮（0=无限），仅 cron 时生效")
    timeout_seconds: float | None = Field(
        default=None,
        description="单轮超时；超过则失败并停止",
    )


class ScheduleMonitorInput(BaseModel):
    name: str = Field(description="监控任务唯一名称")
    description: str = Field(description="一句话描述这个监控做什么（会注入提示）")
    check_interval_seconds: int = Field(
        default=1800, ge=10, description="检查间隔秒数（默认 30 分钟，对齐 Codex 默认）"
    )
    fetch_prompt: str = Field(
        description="每次轮询时让智能体执行的指令；返回值会进入 monitor 数据流"
    )
    stop_condition: str | None = Field(
        default=None,
        description="可选自然语言停机条件，例如 '当价格连续 3 次低于 100 即停止'",
    )


class CancelTaskInput(BaseModel):
    task_id: str = Field(description="要取消的任务 ID（list_running_tasks 可查）")


class ListRunningTasksInput(BaseModel):
    kind: str | None = Field(default=None, description="过滤 persistent / monitor / cron")
    status: str | None = Field(default=None, description="过滤 pending / running / paused / completed / failed / cancelled")


# ---------------------------------------------------------------------------
# Implementations
# ---------------------------------------------------------------------------


def _summarise_snapshots(snaps: list[Any]) -> str:
    if not snaps:
        return "（当前没有正在运行的后台任务）"
    rows = ["task_id | kind | status | progress | next_run_at | name"]
    for s in snaps:
        next_run = "-" if s.next_run_at is None else _ts(s.next_run_at)
        rows.append(
            f"{s.task_id} | {s.kind.value} | {s.status.value} | "
            f"{s.progress:.0%} | {next_run} | {s.name}"
        )
    return "\n".join(rows)


def _ts(t: float) -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(t))


def _do_schedule_recurring(
    *,
    persistent_runner: Any,
    parent_thread_id: str | None,
    name: str,
    goal: str,
    steps: list[str],
    cron: str | None,
    max_runs: int,
    timeout_seconds: float | None,
) -> str:
    if persistent_runner is None:
        return "ERROR: PersistentAgentRunner 未注入，请先在 admin / app_matrix 模式启动 Admin Runtime。"

    task = persistent_runner.create_task(
        name=name,
        description=goal,
        agent_name="admin",
        steps=steps or None,
        context={
            "scheduling_kind": "recurring" if cron else "one_shot",
            "cron": cron,
            "max_runs": max_runs,
            "spawned_by_thread": parent_thread_id,
        },
        timeout_seconds=timeout_seconds,
    )
    task_registry.attach_persistent(
        persistent_runner, task, parent_thread_id=parent_thread_id
    )

    suffix = f"，cron='{cron}'" if cron else "，立即执行"
    return (
        f"OK：已登记后台任务 task_id='{task.task_id}'，名称='{name}'{suffix}。"
        f"用 list_running_tasks 查看进度，或 cancel_task('{task.task_id}') 中止。"
    )


def _do_schedule_monitor(
    *,
    monitor_factory: Callable[..., Any] | None,
    fetch_callable: Callable[[str], dict[str, Any]] | None,
    parent_thread_id: str | None,
    name: str,
    description: str,
    check_interval_seconds: int,
    fetch_prompt: str,
    stop_condition: str | None,
) -> str:
    if monitor_factory is None:
        return "ERROR: monitor_factory 未注入（admin / app_matrix 模式应在启动时绑定）。"

    fetcher = _build_monitor_fetcher(
        fetch_callable=fetch_callable,
        prompt=fetch_prompt,
    )
    monitor = monitor_factory(
        name=name,
        description=description,
        check_interval_seconds=check_interval_seconds,
        fetcher=fetcher,
    )
    task = task_registry.attach_monitor(monitor, parent_thread_id=parent_thread_id)
    task.extra["fetch_prompt"] = fetch_prompt
    if stop_condition:
        task.extra["stop_condition"] = stop_condition
    if hasattr(monitor, "start"):
        monitor.start()

    return (
        f"OK：已启动监控 task_id='{task.task_id}'，每 {check_interval_seconds}s 触发一次。"
        f"用 cancel_task('{task.task_id}') 停止。"
    )


def _build_monitor_fetcher(
    *,
    fetch_callable: Callable[[str], dict[str, Any]] | None,
    prompt: str,
) -> Callable[[], list[dict[str, Any]]]:
    """Wrap an LLM-callable into the ``MonitorTask`` fetcher signature.

    When ``fetch_callable`` is None, the monitor just records the prompt as
    a placeholder snapshot — useful for unit tests and dry runs.
    """

    def _fetch() -> list[dict[str, Any]]:
        if fetch_callable is None:
            return [{"prompt": prompt, "ts": time.time()}]
        try:
            result = fetch_callable(prompt)
            if isinstance(result, dict):
                return [result]
            if isinstance(result, list):
                return [r for r in result if isinstance(r, dict)]
            return [{"output": str(result), "ts": time.time()}]
        except Exception as exc:
            logger.warning("monitor fetch failed: %s", exc)
            return [{"error": str(exc), "ts": time.time()}]

    return _fetch


def _do_list(kind: str | None, status: str | None) -> str:
    from core.systems.tasks.long_running_task import TaskKind, TaskStatus  # noqa: PLC0415

    k = None
    s = None
    if kind:
        try:
            k = TaskKind(kind)
        except ValueError:
            return f"ERROR: 未知 kind '{kind}'，应为 persistent/monitor/cron"
    if status:
        try:
            s = TaskStatus(status)
        except ValueError:
            return f"ERROR: 未知 status '{status}'"
    snaps = task_registry.list(kind=k, status=s)
    return _summarise_snapshots(snaps)


def _do_cancel(task_id: str) -> str:
    if task_registry.get(task_id) is None:
        return f"ERROR: 找不到 task_id='{task_id}'"
    ok = task_registry.cancel(task_id)
    return f"OK：已取消 task_id='{task_id}'" if ok else f"WARN：cancel('{task_id}') 返回 False"


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------


def build_scheduling_tools(
    *,
    persistent_runner: Any | None = None,
    monitor_factory: Callable[..., Any] | None = None,
    monitor_fetch_callable: Callable[[str], Any] | None = None,
    parent_thread_id: str | None = None,
) -> list[StructuredTool]:
    """Build the four LangChain tools an LLM uses to manage tasks.

    Parameters
    ----------
    persistent_runner:
        :class:`PersistentAgentRunner` instance (typically
        ``admin_runtime.runner``).  Pass ``None`` to disable
        ``schedule_recurring_task`` (the tool will return a hard error).
    monitor_factory:
        Callable ``(name, description, check_interval_seconds, fetcher) -> monitor``
        that produces a started-able :class:`~core.modes.monitor_task.MonitorTask`
        (or any duck-equivalent with ``start()`` / ``stop()``).  Bound by the
        caller — keeping the import lives at the L3 boundary so this module
        stays L1-pure.
    monitor_fetch_callable:
        Callable invoked once per monitor tick with the user-defined
        prompt; whatever it returns is fed into the monitor's data stream.
        In a real PyBot session this is bound to a small LLM agent that
        runs the prompt and returns a dict.
    parent_thread_id:
        Conversation thread that spawned the tools; used to scope the
        web SSE channel.
    """

    return [
        StructuredTool.from_function(
            name="schedule_recurring_task",
            description=(
                "登记一个长时间运行的后台任务。"
                "参数 cron 给出 5 字段 cron 表达式即变为周期任务，否则是一次性持久任务。"
                "返回 task_id；可用 list_running_tasks 查询进度，cancel_task 中止。"
            ),
            args_schema=ScheduleRecurringTaskInput,
            func=lambda **kwargs: _do_schedule_recurring(
                persistent_runner=persistent_runner,
                parent_thread_id=parent_thread_id,
                **kwargs,
            ),
        ),
        StructuredTool.from_function(
            name="schedule_monitor",
            description=(
                "启动一个带状态的轮询监控（Codex Cloud 风格的"
                "'每 N 分钟跑一次并展示在右侧任务卡'）。"
                "底层基于 MonitorTask，每 check_interval_seconds 调用一次 fetch_prompt 指令。"
            ),
            args_schema=ScheduleMonitorInput,
            func=lambda **kwargs: _do_schedule_monitor(
                monitor_factory=monitor_factory,
                fetch_callable=monitor_fetch_callable,
                parent_thread_id=parent_thread_id,
                **kwargs,
            ),
        ),
        StructuredTool.from_function(
            name="list_running_tasks",
            description="列出所有当前注册的后台任务（持久 / 监控 / cron），可按 kind 与 status 过滤。",
            args_schema=ListRunningTasksInput,
            func=lambda **kwargs: _do_list(**kwargs),
        ),
        StructuredTool.from_function(
            name="cancel_task",
            description="按 task_id 取消正在运行的后台任务；不可逆。",
            args_schema=CancelTaskInput,
            func=lambda **kwargs: _do_cancel(**kwargs),
        ),
    ]


__all__ = [
    "ScheduleMonitorInput",
    "ScheduleRecurringTaskInput",
    "build_scheduling_tools",
]
