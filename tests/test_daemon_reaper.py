from __future__ import annotations

import time
from unittest.mock import MagicMock

from core.systems.runtime.daemon import BackgroundDaemon, SessionReaper


def test_daemon_runs_job():
    daemon = BackgroundDaemon()
    called = False

    def my_job():
        nonlocal called
        called = True

    daemon.add_job("test_job", 0.05, my_job)
    daemon.start()

    time.sleep(0.15)  # Give it time to run
    daemon.stop()

    assert called is True


def test_daemon_handles_job_exception():
    daemon = BackgroundDaemon()
    called_first = False
    called_second = False

    def failing_job():
        nonlocal called_first
        called_first = True
        raise ValueError("Simulated failure")

    def successful_job():
        nonlocal called_second
        called_second = True

    daemon.add_job("fail", 0.05, failing_job)
    daemon.add_job("success", 0.05, successful_job)
    daemon.start()

    time.sleep(0.15)
    daemon.stop()

    assert called_first is True
    assert called_second is True  # Second job should still run even if first fails


def test_session_reaper_calls_managers():
    mock_lease_manager = MagicMock()
    mock_lease_manager.prune_expired.return_value = 2

    mock_task_queue = MagicMock()
    mock_task_queue.prune_stale_tasks.return_value = 1

    reaper = SessionReaper(lease_manager=mock_lease_manager, task_queue=mock_task_queue)
    reaper.run_reap_cycle()

    mock_lease_manager.prune_expired.assert_called_once()
    mock_task_queue.prune_stale_tasks.assert_called_once()
