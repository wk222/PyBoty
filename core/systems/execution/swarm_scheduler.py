"""Swarm Scheduler -- Layer 1 execution management for asynchronous subagents."""

from __future__ import annotations

import logging
import threading
import time
import traceback
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    from core.modes.agents.subagent_registry import SubagentRegistry, SubagentRunRecord

logger = logging.getLogger(__name__)


@dataclass
class SwarmTask:
    """A managed task running a subagent in the background."""
    run_id: str
    agent_name: str
    task_description: str
    thread: threading.Thread
    started_at: float = field(default_factory=time.time)


class SwarmScheduler:
    """Unified scheduler for background subagent execution.
    
    Provides:
    - Thread management for async subagent runs.
    - Error capturing and bubbling.
    - Status tracking and cleanup.
    """

    def __init__(self, registry: SubagentRegistry):
        self.registry = registry
        self._active_tasks: dict[str, SwarmTask] = {}
        self._lock = threading.Lock()

    def spawn_managed(
        self,
        *,
        agent_name: str,
        task: str,
        invoke_fn: Callable[..., dict[str, Any]],
        invoke_kwargs: dict[str, Any],
        parent_agent_name: str = "root",
        timeout_seconds: float | None = None,
    ) -> str:
        """Spawn a subagent run managed by the scheduler."""
        
        # 1. Register the run
        record = self.registry.spawn(
            agent_name=agent_name,
            thread_id=invoke_kwargs.get("thread_id", "swarm_task"),
            parent_agent_name=parent_agent_name,
            timeout_seconds=timeout_seconds,
        )
        run_id = record.run_id
        
        # 2. Define the background wrapper
        def _target():
            try:
                # Update record in kwargs if needed
                if "subagent_registry" in invoke_kwargs:
                    invoke_kwargs["subagent_registry"] = self.registry
                
                # Execute
                result = invoke_fn(
                    agent_name=agent_name,
                    task=task,
                    **invoke_kwargs
                )
                # Success is handled by the internal invoke or registry.complete
            except Exception as exc:
                error_trace = traceback.format_exc()
                logger.error("Swarm task %s failed: %s\n%s", run_id, exc, error_trace)
                self.registry.fail(run_id, error=str(exc), error_context=error_trace)
            finally:
                with self._lock:
                    if run_id in self._active_tasks:
                        del self._active_tasks[run_id]

        # 3. Start thread
        thread = threading.Thread(
            target=_target,
            name=f"SwarmTask-{agent_name}-{run_id[:8]}",
            daemon=True
        )
        
        with self._lock:
            self._active_tasks[run_id] = SwarmTask(
                run_id=run_id,
                agent_name=agent_name,
                task_description=task,
                thread=thread
            )
            
        thread.start()
        return run_id

    def get_task_status(self, run_id: str) -> dict[str, Any]:
        """Get the current status of a managed task."""
        record = self.registry.get(run_id)
        if not record:
            return {"status": "unknown", "success": False, "error": "Task not found"}
            
        return record.to_dict()

    def wait_for_task(self, run_id: str, timeout: float | None = None) -> dict[str, Any]:
        """Wait for a task to complete and return its result."""
        self.registry.wait(run_id, timeout=timeout)
        return self.get_task_status(run_id)

    def list_active_tasks(self) -> list[dict[str, Any]]:
        """List currently running background tasks."""
        with self._lock:
            return [
                {
                    "run_id": t.run_id,
                    "agent_name": t.agent_name,
                    "task": t.task_description,
                    "runtime_sec": time.time() - t.started_at
                }
                for t in self._active_tasks.values()
            ]
