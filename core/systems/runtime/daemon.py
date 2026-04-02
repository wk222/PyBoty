"""Background Daemon for PyBoty system tasks.

Provides a robust background execution environment for periodic maintenance
tasks, such as the Session Reaper, log rotation, and drift auditing.
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class CronJob:
    """A scheduled background job."""

    name: str
    interval_sec: float
    func: Callable[[], None]
    last_run: float = 0.0


class BackgroundDaemon:
    """Runs periodic maintenance tasks in a background thread."""

    def __init__(self) -> None:
        self._jobs: list[CronJob] = []
        self._running = False
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()

    def add_job(self, name: str, interval_sec: float, func: Callable[[], None]) -> None:
        """Register a new periodic job."""
        with self._lock:
            self._jobs.append(
                CronJob(
                    name=name,
                    interval_sec=interval_sec,
                    func=func,
                )
            )
        logger.debug("Registered daemon job: %s (interval: %ss)", name, interval_sec)

    def start(self) -> None:
        """Start the daemon thread."""
        with self._lock:
            if self._running:
                return
            self._running = True
            self._thread = threading.Thread(
                target=self._run_loop,
                daemon=True,
                name="PyBotDaemon",
            )
            self._thread.start()
        logger.info("Background daemon started.")

    def stop(self, timeout: float = 2.0) -> None:
        """Stop the daemon thread gracefully."""
        with self._lock:
            self._running = False
            thread = self._thread
            self._thread = None

        if thread and thread.is_alive():
            thread.join(timeout=timeout)
        logger.info("Background daemon stopped.")

    def _run_loop(self) -> None:
        """Main loop executing registered jobs."""
        while self._running:
            now = time.time()
            jobs_to_run = []

            with self._lock:
                for job in self._jobs:
                    if now - job.last_run >= job.interval_sec:
                        jobs_to_run.append(job)
                        job.last_run = now

            for job in jobs_to_run:
                if not self._running:
                    break
                try:
                    job.func()
                except Exception as exc:
                    logger.error("Daemon job '%s' failed: %s", job.name, exc, exc_info=True)

            # Sleep briefly to prevent CPU spinning, but allow fast shutdown
            for _ in range(10):
                if not self._running:
                    break
                time.sleep(0.1)


class SessionReaper:
    """Cleans up stale leases and timed-out tasks."""

    def __init__(self, lease_manager: Any = None, task_queue: Any = None):
        self.lease_manager = lease_manager
        self.task_queue = task_queue

    def run_reap_cycle(self) -> None:
        """Execute a single cleanup cycle."""
        reaped_leases = 0
        reaped_tasks = 0

        if self.lease_manager and hasattr(self.lease_manager, "prune_expired"):
            try:
                reaped_leases = self.lease_manager.prune_expired()
            except Exception as exc:
                logger.error("Failed to prune expired leases: %s", exc)

        if self.task_queue and hasattr(self.task_queue, "prune_stale_tasks"):
            try:
                reaped_tasks = self.task_queue.prune_stale_tasks()
            except Exception as exc:
                logger.error("Failed to prune stale tasks: %s", exc)

        if reaped_leases > 0 or reaped_tasks > 0:
            logger.info(
                "SessionReaper cycle complete: %d expired leases removed, %d stale tasks failed.",
                reaped_leases,
                reaped_tasks,
            )
