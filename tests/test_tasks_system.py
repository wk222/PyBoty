from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

import pytest

from core.systems.runtime.event_bus import Event, EventType, event_bus
from core.systems.tasks import (
    LongRunningTask,
    TaskKind,
    TaskRegistry,
    TaskSnapshot,
    TaskStatus,
    build_scheduling_tools,
    task_registry,
)
from core.systems.tasks.task_events import (
    TASK_EVENT_HEARTBEAT,
    TASK_EVENT_SPAWNED,
    TASK_EVENT_STEP,
    TASK_EVENT_TYPES,
    is_task_event,
)
from core.systems.agents.task_snapshot import (
    DEFAULT_CONVERSATION_WINDOW,
    DEFAULT_PINNED_FACTS,
    SNAPSHOT_KEY,
    SnapshotRestoreReport,
    TaskContextSnapshot,
    attach_snapshot,
    capture_snapshot,
    read_snapshot,
    restore_for_resume,
)


# ---------------------------------------------------------------------------
# Test doubles for TaskRegistry / LongRunningTask Tests
# ---------------------------------------------------------------------------

@dataclass
class _FakePersistentStep:
    description: str = "current step"


@dataclass
class _FakePersistentTask:
    task_id: str = "fake-1"
    name: str = "fake task"
    description: str = "test"
    status_value: str = "running"
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    started_at: float | None = None
    completed_at: float | None = None
    heartbeat_at: float | None = None
    progress: float = 0.5
    error: str | None = None
    current_step: _FakePersistentStep | None = field(default_factory=_FakePersistentStep)

    @property
    def status(self) -> Any:
        class _S:
            def __init__(self, value: str) -> None:
                self.value = value

        return _S(self.status_value)


class _FakePersistentRunner:
    def __init__(self, task: _FakePersistentTask) -> None:
        self._task = task
        self.cancel_calls = 0
        self.pause_calls = 0
        self.resume_calls = 0

    def get_task(self, task_id: str) -> _FakePersistentTask | None:
        return self._task if task_id == self._task.task_id else None

    def cancel_task(self, _task_id: str) -> bool:
        self.cancel_calls += 1
        self._task.status_value = "cancelled"
        return True

    def pause_task(self, _task_id: str) -> bool:
        self.pause_calls += 1
        self._task.status_value = "paused"
        return True

    def resume_task(self, _task_id: str) -> bool:
        self.resume_calls += 1
        self._task.status_value = "running"
        return True


@dataclass
class _FakeMonitorConfig:
    name: str = "bda"
    description: str = "Monitor BDA-K L1 1B"
    check_interval_seconds: int = 1800


class _FakeMonitor:
    def __init__(self) -> None:
        self.config = _FakeMonitorConfig()
        self._last_check_time = time.time() - 60
        self._alerts: list[Any] = []
        self._stopped = False
        self._paused = False

        class _State:
            def __init__(self, value: str) -> None:
                self.value = value

        self.state = _State("running")
        self._State = _State

    def stop(self) -> None:
        self._stopped = True
        self.state = self._State("stopped")

    def pause(self) -> None:
        self._paused = True
        self.state = self._State("paused")

    def resume(self) -> None:
        self._paused = False
        self.state = self._State("running")


@dataclass
class _FakeScheduledTask:
    name: str = "nightly"
    cron_expr: str = "0 3 * * *"
    enabled: bool = True
    description: str = "Nightly digest"
    created_at: float = field(default_factory=time.time)
    last_run_at: float | None = None
    run_count: int = 0


class _FakeScheduler:
    def __init__(self) -> None:
        self._tasks: dict[str, _FakeScheduledTask] = {}
        self.removed: list[str] = []

    def add(self, task: _FakeScheduledTask) -> None:
        self._tasks[task.name] = task

    def get_task(self, name: str) -> _FakeScheduledTask | None:
        return self._tasks.get(name)

    def remove_task(self, name: str) -> bool:
        self.removed.append(name)
        return self._tasks.pop(name, None) is not None


# ---------------------------------------------------------------------------
# Test doubles for build_scheduling_tools Tests
# ---------------------------------------------------------------------------

@dataclass
class _FakeStepTools:
    description: str
    status: str = "pending"


@dataclass
class _FakePersistentTaskTools:
    task_id: str
    name: str
    description: str
    agent_name: str
    steps: list[_FakeStepTools]
    context: dict[str, Any]
    timeout_seconds: float | None
    status_value: str = "pending"
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    started_at: float | None = None
    completed_at: float | None = None
    heartbeat_at: float | None = None
    error: str | None = None

    @property
    def status(self) -> Any:
        class _S:
            def __init__(self, value: str) -> None:
                self.value = value

        return _S(self.status_value)

    @property
    def progress(self) -> float:
        if not self.steps:
            return 0.0
        return sum(1 for s in self.steps if s.status == "completed") / len(self.steps)

    @property
    def current_step(self) -> _FakeStepTools | None:
        for s in self.steps:
            if s.status in ("pending", "running"):
                return s
        return None


class _FakePersistentRunnerTools:
    def __init__(self) -> None:
        self.tasks: dict[str, _FakePersistentTaskTools] = {}
        self._counter = 0

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
    ) -> _FakePersistentTaskTools:
        self._counter += 1
        tid = f"pt-{self._counter}"
        task = _FakePersistentTaskTools(
            task_id=tid,
            name=name,
            description=description,
            agent_name=agent_name,
            steps=[_FakeStepTools(description=s) for s in (steps or [])],
            context=context or {},
            timeout_seconds=timeout_seconds,
        )
        self.tasks[tid] = task
        return task

    def get_task(self, task_id: str) -> _FakePersistentTaskTools | None:
        return self.tasks.get(task_id)

    def cancel_task(self, task_id: str) -> bool:
        task = self.tasks.get(task_id)
        if task is None:
            return False
        task.status_value = "cancelled"
        return True

    def pause_task(self, task_id: str) -> bool:
        task = self.tasks.get(task_id)
        if task is None:
            return False
        task.status_value = "paused"
        return True

    def resume_task(self, task_id: str) -> bool:
        task = self.tasks.get(task_id)
        if task is None:
            return False
        task.status_value = "running"
        return True


class _MonitorFactory:
    def __init__(self) -> None:
        self.created: list[Any] = []

    def __call__(
        self,
        *,
        name: str,
        description: str,
        check_interval_seconds: int,
        fetcher: Any,
    ) -> Any:
        from core.modes.monitor_task import MonitorConfig, MonitorTask

        config = MonitorConfig(
            name=name,
            description=description,
            check_interval_seconds=check_interval_seconds,
        )
        monitor = MonitorTask(config, fetcher)
        self.created.append(monitor)
        return monitor


# ---------------------------------------------------------------------------
# Test doubles for Task Snapshot Tests
# ---------------------------------------------------------------------------

@dataclass
class _TaskSnapshotTest:
    context: dict[str, Any] = field(default_factory=dict)


@dataclass
class _FactSnapshotTest:
    content: str


class _RouterSnapshotTest:
    def __init__(self, facts: list[str]) -> None:
        self._facts = facts
        self.calls: list[tuple[str, int]] = []

    def route(self, *, query: str, top_k: int) -> list[Any]:
        self.calls.append((query, top_k))
        return [_FactSnapshotTest(content=t) for t in self._facts[:top_k]]


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def task_events() -> list[Event]:
    captured: list[Event] = []

    def _on_event(event: Event) -> None:
        if is_task_event(event):
            captured.append(event)

    event_bus.subscribe(EventType.SCHEDULE_RUN, _on_event)
    yield captured
    event_bus.unsubscribe(EventType.SCHEDULE_RUN, _on_event)


@pytest.fixture
def registry() -> TaskRegistry:
    reg = TaskRegistry()
    yield reg
    reg.clear()


@pytest.fixture(autouse=False)
def _clean_registry_for_tools():
    task_registry.clear()
    yield
    for t in list(task_registry):
        try:
            t.cancel()
        except Exception:
            pass
    task_registry.clear()


@pytest.fixture
def runner_tools() -> _FakePersistentRunnerTools:
    return _FakePersistentRunnerTools()


@pytest.fixture
def monitor_mgr() -> _MonitorFactory:
    return _MonitorFactory()


# ---------------------------------------------------------------------------
# 1. Long-Running Task Subsystem Tests (formerly test_long_running_task.py)
# ---------------------------------------------------------------------------

class TestLongRunningTaskClass:
    def test_persistent_snapshot_round_trip(self, registry: TaskRegistry):
        task = _FakePersistentTask()
        runner = _FakePersistentRunner(task)
        handle = registry.attach_persistent(runner, task, parent_thread_id="thread-A")
        snap = handle.snapshot()
        assert snap.task_id == "fake-1"
        assert snap.kind is TaskKind.PERSISTENT
        assert snap.status is TaskStatus.RUNNING
        assert snap.parent_thread_id == "thread-A"
        assert snap.progress == 0.5
        assert snap.last_step == "current step"

    def test_monitor_snapshot_includes_next_run(self, registry: TaskRegistry):
        monitor = _FakeMonitor()
        handle = registry.attach_monitor(monitor)
        snap = handle.snapshot()
        assert snap.kind is TaskKind.MONITOR
        assert snap.task_id == "monitor:bda"
        assert snap.next_run_at is not None
        assert snap.next_run_at > snap.heartbeat_at

    def test_cron_snapshot_disabled_maps_to_paused(self, registry: TaskRegistry):
        sched = _FakeScheduler()
        sched.add(_FakeScheduledTask(name="weekly", enabled=False))
        handle = registry.attach_cron(sched, sched.get_task("weekly"))
        snap = handle.snapshot()
        assert snap.kind is TaskKind.CRON
        assert snap.status is TaskStatus.PAUSED

    def test_snapshot_to_dict_serialisable(self, registry: TaskRegistry):
        task = _FakePersistentTask()
        handle = registry.attach_persistent(_FakePersistentRunner(task), task)
        data = handle.snapshot().to_dict()
        assert data["status"] == "running"
        assert data["kind"] == "persistent"
        assert isinstance(data["tags"], list)

    def test_register_emits_spawned_event(self, registry: TaskRegistry, task_events):
        monitor = _FakeMonitor()
        registry.attach_monitor(monitor)
        spawned = [e for e in task_events if e.payload["task_event"] == TASK_EVENT_SPAWNED]
        assert len(spawned) == 1
        assert spawned[0].payload["task_id"] == "monitor:bda"
        assert "snapshot" in spawned[0].payload

    def test_touch_emits_heartbeat_and_step(self, registry: TaskRegistry, task_events):
        task = _FakePersistentTask()
        registry.attach_persistent(_FakePersistentRunner(task), task, parent_thread_id="t1")
        task_events.clear()
        registry.touch(task.task_id, step="downloaded.csv", progress=0.7)
        kinds = [e.payload["task_event"] for e in task_events]
        assert TASK_EVENT_HEARTBEAT in kinds
        assert TASK_EVENT_STEP in kinds
        step_event = next(e for e in task_events if e.payload["task_event"] == TASK_EVENT_STEP)
        assert step_event.payload["step"] == "downloaded.csv"
        assert step_event.payload["progress"] == 0.7

    def test_touch_unknown_task_is_noop(self, registry: TaskRegistry, task_events):
        registry.touch("not-there", step="oops")
        assert task_events == []

    def test_list_filters_by_kind_status_thread(self, registry: TaskRegistry):
        persistent_task = _FakePersistentTask()
        registry.attach_persistent(_FakePersistentRunner(persistent_task), persistent_task, parent_thread_id="A")
        monitor = _FakeMonitor()
        registry.attach_monitor(monitor, parent_thread_id="B")

        all_snaps = registry.list()
        assert len(all_snaps) == 2

        only_monitor = registry.list(kind=TaskKind.MONITOR)
        assert len(only_monitor) == 1
        assert only_monitor[0].kind is TaskKind.MONITOR

        only_thread_a = registry.list(thread_id="A")
        assert len(only_thread_a) == 1
        assert only_thread_a[0].parent_thread_id == "A"

    def test_cancel_pause_resume_propagate(self, registry: TaskRegistry, task_events):
        task = _FakePersistentTask()
        runner = _FakePersistentRunner(task)
        registry.attach_persistent(runner, task)
        assert registry.pause(task.task_id) is True
        assert runner.pause_calls == 1
        assert registry.resume(task.task_id) is True
        assert runner.resume_calls == 1
        assert registry.cancel(task.task_id) is True
        assert runner.cancel_calls == 1
        assert any(
            e.payload["task_event"] == "task.cancelled" for e in task_events
        )

    def test_unregister_removes_task(self, registry: TaskRegistry):
        task = _FakePersistentTask()
        registry.attach_persistent(_FakePersistentRunner(task), task)
        assert len(registry) == 1
        registry.unregister(task.task_id)
        assert len(registry) == 0
        assert registry.get(task.task_id) is None

    def test_task_event_types_are_distinct(self):
        assert len(set(TASK_EVENT_TYPES)) == len(TASK_EVENT_TYPES)
        for name in TASK_EVENT_TYPES:
            assert name.startswith("task.")

    def test_is_task_event_filters_unrelated_events(self):
        other = Event(type=EventType.TOOL_CALL, payload={"foo": 1})
        assert is_task_event(other) is False
        task_evt = Event(
            type=EventType.SCHEDULE_RUN,
            payload={"task_event": "task.heartbeat", "task_id": "x"},
        )
        assert is_task_event(task_evt) is True
        assert is_task_event(task_evt, task_id="x") is True
        assert is_task_event(task_evt, task_id="y") is False

    def test_long_running_task_rejects_bad_snapshot(self):
        bad = LongRunningTask(
            task_id="x",
            kind=TaskKind.PERSISTENT,
            handle=None,
            snapshot_fn=lambda _h: "not a snapshot",
        )
        with pytest.raises(TypeError):
            bad.snapshot()

    def test_long_running_task_inherits_thread_id_when_missing(self):
        snap = TaskSnapshot(
            task_id="x",
            kind=TaskKind.PERSISTENT,
            name="x",
            status=TaskStatus.RUNNING,
            created_at=0,
            updated_at=0,
        )
        task = LongRunningTask(
            task_id="x",
            kind=TaskKind.PERSISTENT,
            handle=None,
            snapshot_fn=lambda _h: snap,
            parent_thread_id="thread-Z",
        )
        out = task.snapshot()
        assert out.parent_thread_id == "thread-Z"


# ---------------------------------------------------------------------------
# 2. Task Scheduling Tools Tests (formerly test_task_scheduling_tools.py)
# ---------------------------------------------------------------------------

class TestTaskSchedulingToolsClass:
    @pytest.mark.usefixtures("_clean_registry_for_tools")
    def test_schedule_recurring_creates_persistent_task(self, runner_tools):
        tools = build_scheduling_tools(persistent_runner=runner_tools, parent_thread_id="thread-1")
        schedule = next(t for t in tools if t.name == "schedule_recurring_task")
        msg = schedule.invoke(
            {
                "name": "weekly_digest",
                "goal": "Summarise weekly metrics",
                "steps": ["fetch", "summarise", "publish"],
                "cron": "0 8 * * 1",
                "max_runs": 0,
                "timeout_seconds": 600.0,
            }
        )
        assert "OK" in msg
        assert "task_id='pt-1'" in msg
        snaps = task_registry.list()
        assert len(snaps) == 1
        snap = snaps[0]
        assert snap.kind is TaskKind.PERSISTENT
        assert snap.parent_thread_id == "thread-1"
        assert runner_tools.tasks["pt-1"].context["cron"] == "0 8 * * 1"
        assert runner_tools.tasks["pt-1"].context["scheduling_kind"] == "recurring"

    @pytest.mark.usefixtures("_clean_registry_for_tools")
    def test_schedule_recurring_without_cron_marks_one_shot(self, runner_tools):
        tools = build_scheduling_tools(persistent_runner=runner_tools)
        schedule = next(t for t in tools if t.name == "schedule_recurring_task")
        schedule.invoke({"name": "ad_hoc", "goal": "Just once"})
        assert runner_tools.tasks["pt-1"].context["scheduling_kind"] == "one_shot"

    @pytest.mark.usefixtures("_clean_registry_for_tools")
    def test_schedule_recurring_without_runner_returns_error(self):
        tools = build_scheduling_tools(persistent_runner=None)
        schedule = next(t for t in tools if t.name == "schedule_recurring_task")
        msg = schedule.invoke({"name": "x", "goal": "y"})
        assert "ERROR" in msg

    @pytest.mark.usefixtures("_clean_registry_for_tools")
    def test_schedule_monitor_starts_and_registers(self, monitor_mgr):
        captured: list[str] = []

        def fake_fetch(prompt: str) -> dict[str, Any]:
            captured.append(prompt)
            return {"value": 42, "ts": time.time()}

        tools = build_scheduling_tools(
            monitor_factory=monitor_mgr,
            monitor_fetch_callable=fake_fetch,
            parent_thread_id="thread-2",
        )
        schedule = next(t for t in tools if t.name == "schedule_monitor")

        msg = schedule.invoke(
            {
                "name": "bda",
                "description": "Monitor BDA-K L1 1B",
                "check_interval_seconds": 30,
                "fetch_prompt": "Run one eval step and report",
            }
        )
        try:
            assert "OK" in msg
            assert "monitor:bda" in msg
            snap = task_registry.get("monitor:bda").snapshot()
            assert snap.kind is TaskKind.MONITOR
            assert snap.parent_thread_id == "thread-2"

            monitor = monitor_mgr.created[0]
            deadline = time.time() + 2.0
            while time.time() < deadline and not captured:
                time.sleep(0.05)
            assert captured, "fetch callable was never invoked"
            assert all(c == "Run one eval step and report" for c in captured)
            assert len(monitor._last_data) >= 1
            assert monitor._last_data[0]["value"] == 42
        finally:
            for m in monitor_mgr.created:
                m.stop()

    @pytest.mark.usefixtures("_clean_registry_for_tools")
    def test_schedule_monitor_without_factory_returns_error(self):
        tools = build_scheduling_tools(monitor_factory=None)
        schedule = next(t for t in tools if t.name == "schedule_monitor")
        msg = schedule.invoke(
            {
                "name": "x",
                "description": "y",
                "fetch_prompt": "z",
            }
        )
        assert "ERROR" in msg

    @pytest.mark.usefixtures("_clean_registry_for_tools")
    def test_list_running_tasks_returns_table(self, runner_tools):
        tools = build_scheduling_tools(persistent_runner=runner_tools)
        schedule = next(t for t in tools if t.name == "schedule_recurring_task")
        schedule.invoke({"name": "a", "goal": "first"})
        schedule.invoke({"name": "b", "goal": "second"})

        listing = next(t for t in tools if t.name == "list_running_tasks")
        out = listing.invoke({})
        assert "task_id" in out
        assert "pt-1" in out and "pt-2" in out

    @pytest.mark.usefixtures("_clean_registry_for_tools")
    def test_list_running_tasks_filters(self, runner_tools):
        tools = build_scheduling_tools(persistent_runner=runner_tools)
        schedule = next(t for t in tools if t.name == "schedule_recurring_task")
        schedule.invoke({"name": "a", "goal": "first"})
        listing = next(t for t in tools if t.name == "list_running_tasks")

        out = listing.invoke({"kind": "monitor"})
        assert "没有" in out or "没有正在运行" in out

    @pytest.mark.usefixtures("_clean_registry_for_tools")
    def test_list_running_tasks_rejects_unknown_kind(self, runner_tools):
        tools = build_scheduling_tools(persistent_runner=runner_tools)
        listing = next(t for t in tools if t.name == "list_running_tasks")
        out = listing.invoke({"kind": "made_up"})
        assert "ERROR" in out

    @pytest.mark.usefixtures("_clean_registry_for_tools")
    def test_cancel_task_propagates(self, runner_tools):
        tools = build_scheduling_tools(persistent_runner=runner_tools)
        schedule = next(t for t in tools if t.name == "schedule_recurring_task")
        cancel = next(t for t in tools if t.name == "cancel_task")
        schedule.invoke({"name": "a", "goal": "first"})

        out = cancel.invoke({"task_id": "pt-1"})
        assert "OK" in out
        assert runner_tools.tasks["pt-1"].status_value == "cancelled"
        snap = task_registry.get("pt-1").snapshot()
        assert snap.status is TaskStatus.CANCELLED

    @pytest.mark.usefixtures("_clean_registry_for_tools")
    def test_cancel_task_unknown_returns_error(self, runner_tools):
        tools = build_scheduling_tools(persistent_runner=runner_tools)
        cancel = next(t for t in tools if t.name == "cancel_task")
        out = cancel.invoke({"task_id": "not-there"})
        assert "ERROR" in out


# ---------------------------------------------------------------------------
# 3. Task Context Snapshot Tests (formerly test_task_snapshot.py)
# ---------------------------------------------------------------------------

class TestTaskSnapshotClass:
    def test_capture_with_router_and_window(self):
        msgs = [{"role": "user", "content": f"m{i}"} for i in range(20)]
        router = _RouterSnapshotTest(["fact-A", "fact-B", "fact-C", "fact-D"])
        snap = capture_snapshot(
            conversation_window=msgs,
            memory_md_text="hello world",
            memory_router=router,
            spawn_query="weekly digest",
            canvas="balanced",
            pinned_top_k=3,
            window_size=5,
        )
        assert isinstance(snap, TaskContextSnapshot)
        assert snap.conversation_window == msgs[-5:]
        assert snap.pinned_facts == ["fact-A", "fact-B", "fact-C"]
        assert snap.memory_md_sha1 is not None
        assert snap.canvas == "balanced"
        assert router.calls == [("weekly digest", 3)]

    def test_capture_without_router_skips_pinning(self):
        snap = capture_snapshot(memory_md_text="x", spawn_query=None, memory_router=None)
        assert snap.pinned_facts == []
        assert snap.memory_md_sha1 is not None

    def test_capture_router_failure_is_swallowed(self):
        class _BadRouter:
            def route(self, *, query: str, top_k: int) -> list[Any]:
                raise RuntimeError("boom")

        snap = capture_snapshot(
            memory_router=_BadRouter(), spawn_query="x", memory_md_text="y"
        )
        assert snap.pinned_facts == []

    def test_attach_and_read_round_trip(self):
        task = _TaskSnapshotTest()
        snap = capture_snapshot(memory_md_text="hi", spawn_query=None)
        attach_snapshot(task, snap)
        assert SNAPSHOT_KEY in task.context
        out = read_snapshot(task)
        assert out is not None
        assert out.memory_md_sha1 == snap.memory_md_sha1

    def test_attach_requires_context_attribute(self):
        class _NoCtx:
            pass

        with pytest.raises(TypeError):
            attach_snapshot(_NoCtx(), capture_snapshot())

    def test_read_snapshot_returns_none_when_missing(self):
        assert read_snapshot(_TaskSnapshotTest()) is None
        assert read_snapshot(_TaskSnapshotTest(context={"foo": 1})) is None

    def test_restore_reports_no_snapshot(self):
        rep = restore_for_resume(_TaskSnapshotTest(), current_memory_md_text="x")
        assert rep.found is False

    def test_restore_detects_drift(self):
        task = _TaskSnapshotTest()
        snap = capture_snapshot(memory_md_text="original")
        attach_snapshot(task, snap)
        rep = restore_for_resume(task, current_memory_md_text="changed")
        assert rep.found
        assert rep.drift_detected
        assert "drifted" in rep.note

    def test_restore_no_drift_when_text_unchanged(self):
        task = _TaskSnapshotTest()
        snap = capture_snapshot(memory_md_text="same")
        attach_snapshot(task, snap)
        rep = restore_for_resume(task, current_memory_md_text="same")
        assert rep.found
        assert rep.drift_detected is False
        assert "unchanged" in rep.note

    def test_restore_age_seconds_is_non_negative(self):
        task = _TaskSnapshotTest()
        snap = capture_snapshot(memory_md_text="x")
        snap.captured_at = time.time() - 10
        attach_snapshot(task, snap)
        rep = restore_for_resume(task, current_memory_md_text="x")
        assert rep.age_seconds >= 9.0

    def test_restore_pinned_count_propagates(self):
        task = _TaskSnapshotTest()
        router = _RouterSnapshotTest(["a", "b", "c"])
        snap = capture_snapshot(
            memory_router=router,
            spawn_query="q",
            memory_md_text="x",
            pinned_top_k=2,
        )
        attach_snapshot(task, snap)
        rep = restore_for_resume(task, current_memory_md_text="x")
        assert rep.pinned_facts_used == 2

    def test_defaults_constants(self):
        assert DEFAULT_CONVERSATION_WINDOW > 0
        assert DEFAULT_PINNED_FACTS > 0
