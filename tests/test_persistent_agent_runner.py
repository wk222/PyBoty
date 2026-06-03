"""Tests for PersistentAgentRunner — Manus-style durable execution."""

from __future__ import annotations

import tempfile

from core.modes.agents.persistent_agent_runner import (
    PersistentAgentRunner,
    PersistentTask,
    PersistentTaskStatus,
    PersistentTaskStep,
)


class TestPersistentTask:
    def test_create_and_serialize(self):
        task = PersistentTask(
            task_id="t1",
            name="research",
            description="Do research",
            agent_name="researcher",
        )
        task.add_step("Gather data")
        task.add_step("Analyze data")
        d = task.to_dict()
        assert d["task_id"] == "t1"
        assert len(d["steps"]) == 2
        assert d["progress"] == 0.0

    def test_from_dict_roundtrip(self):
        task = PersistentTask(
            task_id="t2",
            name="build",
            description="Build app",
            agent_name="builder",
            max_steps=10,
        )
        task.add_step("Design")
        task.add_step("Code")
        d = task.to_dict()
        restored = PersistentTask.from_dict(d)
        assert restored.task_id == "t2"
        assert len(restored.steps) == 2
        assert restored.max_steps == 10

    def test_progress_tracking(self):
        task = PersistentTask(task_id="t3", name="x", description="x", agent_name="a")
        task.add_step("s1")
        task.add_step("s2")
        assert task.progress == 0.0
        task.steps[0].status = "completed"
        assert task.progress == 0.5
        task.steps[1].status = "completed"
        assert task.progress == 1.0

    def test_current_step(self):
        task = PersistentTask(task_id="t4", name="x", description="x", agent_name="a")
        task.add_step("s1")
        task.add_step("s2")
        assert task.current_step.step_id == "step_1"
        task.steps[0].status = "completed"
        assert task.current_step.step_id == "step_2"
        task.steps[1].status = "completed"
        assert task.current_step is None


class TestPersistentAgentRunner:
    def _make_runner(self, tmpdir: str) -> PersistentAgentRunner:
        return PersistentAgentRunner(tmpdir)

    def test_create_and_list(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            runner = self._make_runner(tmpdir)
            task = runner.create_task(
                name="test_task",
                description="test",
                agent_name="agent1",
                steps=["Step 1", "Step 2"],
            )
            assert task.task_id
            assert len(runner.list_tasks()) == 1

    def test_execute_step(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            runner = self._make_runner(tmpdir)
            task = runner.create_task(
                name="exec_test",
                description="test",
                agent_name="agent1",
                steps=["Step 1", "Step 2"],
            )

            def step_fn(step: PersistentTaskStep, ctx: dict) -> dict:
                return {"result": f"done_{step.step_id}"}

            step = runner.execute_step(task.task_id, step_fn)
            assert step.status == "completed"
            assert step.output_data["result"] == "done_step_1"

    def test_run_all_steps(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            runner = self._make_runner(tmpdir)
            task = runner.create_task(
                name="run_all",
                description="test",
                agent_name="agent1",
                steps=["A", "B", "C"],
            )
            call_count = 0

            def step_fn(step: PersistentTaskStep, ctx: dict) -> dict:
                nonlocal call_count
                call_count += 1
                return {"step": step.step_id}

            result = runner.run_all_steps(task.task_id, step_fn)
            assert result.status == PersistentTaskStatus.COMPLETED
            assert call_count == 3
            assert result.progress == 1.0

    def test_step_failure_stops(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            runner = self._make_runner(tmpdir)
            task = runner.create_task(
                name="fail_test",
                description="test",
                agent_name="agent1",
                steps=["OK", "FAIL", "SKIP"],
            )

            def step_fn(step: PersistentTaskStep, ctx: dict) -> dict:
                if step.description == "FAIL":
                    raise RuntimeError("boom")
                return {"ok": True}

            result = runner.run_all_steps(task.task_id, step_fn)
            assert result.status == PersistentTaskStatus.FAILED
            assert result.steps[0].status == "completed"
            assert result.steps[1].status == "failed"
            assert result.steps[2].status == "pending"

    def test_persistence_across_restarts(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            runner1 = self._make_runner(tmpdir)
            task = runner1.create_task(
                name="persist_test",
                description="test",
                agent_name="agent1",
                steps=["Step 1"],
            )
            tid = task.task_id

            runner2 = self._make_runner(tmpdir)
            loaded = runner2.get_task(tid)
            assert loaded is not None
            assert loaded.name == "persist_test"
            assert len(loaded.steps) == 1

    def test_pause_and_resume(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            runner = self._make_runner(tmpdir)
            task = runner.create_task(
                name="pause_test",
                description="test",
                agent_name="agent1",
                steps=["A", "B"],
            )
            runner.execute_step(task.task_id, lambda s, c: {"ok": True})
            assert task.status == PersistentTaskStatus.RUNNING

            assert runner.pause_task(task.task_id)
            assert task.status == PersistentTaskStatus.PAUSED

            assert runner.resume_task(task.task_id)
            assert task.status == PersistentTaskStatus.RUNNING

    def test_cancel(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            runner = self._make_runner(tmpdir)
            task = runner.create_task(
                name="cancel_test",
                description="test",
                agent_name="agent1",
                steps=["A"],
            )
            assert runner.cancel_task(task.task_id)
            assert task.status == PersistentTaskStatus.CANCELLED

    def test_get_resumable_tasks(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            runner = self._make_runner(tmpdir)
            t1 = runner.create_task(name="a", description="a", agent_name="a", steps=["x", "y"])
            runner.create_task(name="b", description="b", agent_name="b", steps=["x"])
            runner.execute_step(t1.task_id, lambda s, c: {"ok": True})
            runner.pause_task(t1.task_id)

            resumable = runner.get_resumable_tasks()
            assert len(resumable) == 1
            assert resumable[0].task_id == t1.task_id

    def test_context_accumulation(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            runner = self._make_runner(tmpdir)
            task = runner.create_task(
                name="ctx_test",
                description="test",
                agent_name="agent1",
                steps=["Gather", "Process"],
                context={"initial": True},
            )

            def step_fn(step: PersistentTaskStep, ctx: dict) -> dict:
                if step.description == "Gather":
                    return {"data": [1, 2, 3]}
                return {"processed": sum(ctx.get("data", []))}

            runner.run_all_steps(task.task_id, step_fn)
            assert task.context["processed"] == 6
            assert task.context["initial"] is True

    def test_execute_step_supports_separate_context_update(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            runner = self._make_runner(tmpdir)
            task = runner.create_task(
                name="memory_test",
                description="test",
                agent_name="agent1",
                steps=["Summarize"],
                context={"existing": True},
            )

            def step_fn(step: PersistentTaskStep, ctx: dict) -> dict:
                assert ctx["existing"] is True
                return {
                    "__step_output__": {"raw": "x" * 1000},
                    "__context_update__": {"summary": "short note"},
                }

            step = runner.execute_step(task.task_id, step_fn)
            assert step is not None
            assert step.status == "completed"
            assert step.output_data == {"raw": "x" * 1000}
            assert task.context["summary"] == "short note"
            assert task.final_output == {"raw": "x" * 1000}

    def test_merge_context_updates_task_in_place(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            runner = self._make_runner(tmpdir)
            task = runner.create_task(
                name="merge_ctx",
                description="test",
                agent_name="agent1",
                steps=["A"],
                context={"a": 1},
            )

            assert runner.merge_context(task.task_id, {"b": 2, "c": 3}) is True
            assert task.context == {"a": 1, "b": 2, "c": 3}

    def test_replace_pending_steps_reopens_completed_task(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            runner = self._make_runner(tmpdir)
            task = runner.create_task(
                name="replan_task",
                description="test",
                agent_name="agent1",
                steps=["Initial"],
            )

            runner.execute_step(task.task_id, lambda s, c: {"done": True})
            assert task.status == PersistentTaskStatus.COMPLETED

            assert runner.replace_pending_steps(task.task_id, ["New step", "Wrap up"]) is True
            assert task.status == PersistentTaskStatus.RUNNING
            assert task.final_output is None
            assert [step.description for step in task.steps] == ["Initial", "New step", "Wrap up"]
            assert task.steps[0].status == "completed"
            assert task.steps[1].status == "pending"
