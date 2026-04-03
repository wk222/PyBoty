"""Tests for core.assets.workflows.task_queue — async task queue and checkpointer factory."""

from __future__ import annotations

import time

import pytest

from core.assets.workflows.task_queue import (
    CheckpointerFactory,
    TaskHandle,
    TaskQueue,
    TaskStatus,
)


@pytest.fixture
def queue():
    q = TaskQueue(max_workers=2, max_history=50)
    yield q
    q.shutdown(wait=True)


class TestTaskSubmit:
    def test_submit_returns_handle(self, queue):
        handle = queue.submit(lambda: 42, name="answer")
        assert isinstance(handle, TaskHandle)
        assert handle.name == "answer"
        assert handle.task_id

    def test_submit_executes_task(self, queue):
        handle = queue.submit(lambda: "done", name="simple")
        info = queue.wait(handle.task_id, timeout=5)
        assert info is not None
        assert info.status == TaskStatus.COMPLETED
        assert info.result == "done"

    def test_submit_with_args(self, queue):
        def add(a, b):
            return a + b

        handle = queue.submit(add, 3, 4, name="add")
        info = queue.wait(handle.task_id, timeout=5)
        assert info is not None
        assert info.result == 7

    def test_submit_with_kwargs(self, queue):
        def greet(name="world"):
            return f"hello {name}"

        handle = queue.submit(greet, name="greet", metadata={"priority": "high"})
        info = queue.wait(handle.task_id, timeout=5)
        assert info is not None
        assert info.result == "hello world"

    def test_failed_task(self, queue):
        def fail():
            raise ValueError("intentional")

        handle = queue.submit(fail, name="failing")
        info = queue.wait(handle.task_id, timeout=5)
        assert info is not None
        assert info.status == TaskStatus.FAILED
        assert "intentional" in info.error

    def test_task_has_timestamps(self, queue):
        handle = queue.submit(lambda: time.sleep(0.01), name="timed")
        info = queue.wait(handle.task_id, timeout=5)
        assert info is not None
        assert info.created_at > 0
        assert info.started_at is not None
        assert info.completed_at is not None
        assert info.started_at >= info.created_at
        assert info.completed_at >= info.started_at


class TestTaskStatus:
    def test_get_status_unknown(self, queue):
        assert queue.get_status("nonexistent") is None

    def test_get_status_after_submit(self, queue):
        handle = queue.submit(lambda: time.sleep(0.5), name="slow")
        info = queue.get_status(handle.task_id)
        assert info is not None
        assert info.status in (TaskStatus.PENDING, TaskStatus.RUNNING)
        queue.wait(handle.task_id, timeout=5)


class TestTaskCancel:
    def test_cancel_completed(self, queue):
        handle = queue.submit(lambda: 1, name="fast")
        queue.wait(handle.task_id, timeout=5)
        assert queue.cancel(handle.task_id) is False

    def test_cancel_nonexistent(self, queue):
        assert queue.cancel("nope") is False


class TestTaskList:
    def test_list_active(self, queue):
        import threading

        event = threading.Event()
        handle = queue.submit(lambda: event.wait(5), name="blocking")
        time.sleep(0.1)
        active = queue.list_active()
        assert len(active) >= 1
        assert any(t.task_id == handle.task_id for t in active)
        event.set()
        queue.wait(handle.task_id, timeout=5)

    def test_list_all(self, queue):
        queue.submit(lambda: 1, name="t1")
        queue.submit(lambda: 2, name="t2")
        time.sleep(0.5)
        all_tasks = queue.list_all()
        assert len(all_tasks) >= 2

    def test_summary(self, queue):
        queue.submit(lambda: 1, name="t1")
        queue.submit(lambda: 2, name="t2")
        time.sleep(0.5)
        summary = queue.get_summary()
        assert isinstance(summary, dict)


class TestTaskWait:
    def test_wait_with_timeout(self, queue):
        import threading

        event = threading.Event()
        handle = queue.submit(lambda: event.wait(10), name="long")
        info = queue.wait(handle.task_id, timeout=0.1)
        assert info is not None
        assert info.status in (TaskStatus.PENDING, TaskStatus.RUNNING)
        event.set()
        queue.wait(handle.task_id, timeout=5)

    def test_wait_nonexistent(self, queue):
        result = queue.wait("nope", timeout=0.1)
        assert result is None


class TestPruneHistory:
    def test_prune_keeps_within_limit(self):
        q = TaskQueue(max_workers=1, max_history=5)
        for i in range(10):
            h = q.submit(lambda x=i: x, name=f"task_{i}")
            q.wait(h.task_id, timeout=5)

        all_tasks = q.list_all()
        assert len(all_tasks) <= 6
        q.shutdown()


class TestCheckpointerFactory:
    def test_sqlite_default(self, tmp_path):
        db_path = str(tmp_path / "test.db")
        saver = CheckpointerFactory.create({"type": "sqlite", "path": db_path})
        assert saver is not None

    def test_sqlite_no_config(self):
        saver = CheckpointerFactory.create()
        assert saver is not None

    def test_postgres_falls_back_to_sqlite(self):
        saver = CheckpointerFactory.create(
            {
                "type": "postgres",
                "connection_string": "postgresql://user:pass@localhost/db",
            }
        )
        assert saver is not None
