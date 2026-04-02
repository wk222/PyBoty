"""Tests for workflow persistence enhancements:

- Cron matching (standard 5-field)
- ScheduledTask.run_once_at
- PyFlowEngine.recover_paused_workflows
- PyFlowEngine.pause_workflow / cancel_workflow
- Approval timeout metadata
"""

from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime

from core.task_scheduler import ScheduledTask, _cron_field_matches, cron_matches

# ── cron_matches tests ────────────────────────────────────────────


class TestCronMatches:
    def test_every_minute(self):
        dt = datetime(2026, 3, 21, 14, 30)
        assert cron_matches("* * * * *", dt) is True

    def test_specific_minute_hour(self):
        dt = datetime(2026, 3, 21, 9, 15)
        assert cron_matches("15 9 * * *", dt) is True
        assert cron_matches("30 9 * * *", dt) is False

    def test_every_5_minutes(self):
        dt = datetime(2026, 3, 21, 10, 0)
        assert cron_matches("*/5 * * * *", dt) is True
        dt2 = datetime(2026, 3, 21, 10, 3)
        assert cron_matches("*/5 * * * *", dt2) is False

    def test_range(self):
        dt = datetime(2026, 3, 21, 10, 15)
        assert cron_matches("10-20 * * * *", dt) is True
        assert cron_matches("0-5 * * * *", dt) is False

    def test_list(self):
        dt = datetime(2026, 3, 21, 10, 15)
        assert cron_matches("0,15,30,45 * * * *", dt) is True
        assert cron_matches("0,10,20,40 * * * *", dt) is False

    def test_day_of_month(self):
        dt = datetime(2026, 3, 21, 10, 0)
        assert cron_matches("0 10 21 * *", dt) is True
        assert cron_matches("0 10 22 * *", dt) is False

    def test_month(self):
        dt = datetime(2026, 3, 21, 10, 0)
        assert cron_matches("0 10 * 3 *", dt) is True
        assert cron_matches("0 10 * 4 *", dt) is False

    def test_day_of_week(self):
        dt = datetime(2026, 3, 21, 10, 0)  # Saturday (5 in 0=Mon)
        dow = dt.weekday()  # 5
        assert cron_matches(f"0 10 * * {dow}", dt) is True
        assert cron_matches(f"0 10 * * {(dow + 1) % 7}", dt) is False

    def test_invalid_format(self):
        dt = datetime(2026, 3, 21, 10, 0)
        assert cron_matches("invalid", dt) is False
        assert cron_matches("* * *", dt) is False

    def test_step_with_range(self):
        dt = datetime(2026, 3, 21, 10, 6)
        assert cron_matches("2-10/2 * * * *", dt) is True
        dt2 = datetime(2026, 3, 21, 10, 7)
        assert cron_matches("2-10/2 * * * *", dt2) is False


class TestCronFieldMatches:
    def test_star(self):
        assert _cron_field_matches("*", 5) is True

    def test_exact(self):
        assert _cron_field_matches("10", 10) is True
        assert _cron_field_matches("10", 11) is False

    def test_step(self):
        assert _cron_field_matches("*/10", 20) is True
        assert _cron_field_matches("*/10", 25) is False


# ── ScheduledTask run_once_at tests ───────────────────────────────


class TestScheduledTaskRunOnce:
    def test_run_once_at_in_dict(self):
        task = ScheduledTask(
            name="once", description="d", cron="* * * * *",
            prompt="p", run_once_at=1234567890.0,
        )
        d = task.to_dict()
        assert d["run_once_at"] == 1234567890.0

    def test_run_once_at_not_in_dict_when_none(self):
        task = ScheduledTask(name="x", description="d", cron="*", prompt="p")
        d = task.to_dict()
        assert "run_once_at" not in d


# ── PyFlowEngine pause/cancel tests ──────────────────────────────


class TestPyFlowEnginePauseCancel:
    def _make_engine(self, tmpdir: str):
        from core.pyflow_engine import PyFlowEngine
        return PyFlowEngine(workspace_dir=tmpdir)

    def test_pause_nonexistent(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            engine = self._make_engine(tmpdir)
            result = engine.pause_workflow("no_such_id")
            assert result["success"] is False

    def test_cancel_nonexistent(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            engine = self._make_engine(tmpdir)
            result = engine.cancel_workflow("no_such_id")
            assert result["success"] is False


# ── Recover paused workflows test ─────────────────────────────────


class TestRecoverPausedWorkflows:
    def test_recover_empty_dir(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            from core.pyflow_engine import PyFlowEngine
            engine = PyFlowEngine(workspace_dir=tmpdir)
            recovered = engine.recover_paused_workflows()
            assert recovered == []

    def test_recover_skips_completed(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            from core.pyflow_engine import PyFlowEngine
            engine = PyFlowEngine(workspace_dir=tmpdir)
            runs_dir = os.path.join(engine.workflows_dir, ".runs")
            os.makedirs(runs_dir, exist_ok=True)
            data = {
                "id": "done_wf",
                "name": "completed_one",
                "status": "completed",
                "nodes": {},
                "edges": [],
            }
            with open(os.path.join(runs_dir, "done_wf.json"), "w") as f:
                json.dump(data, f)
            recovered = engine.recover_paused_workflows()
            assert recovered == []
