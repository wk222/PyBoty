"""Pause/Resume protocol for agent and team execution.

Enables graceful pausing and resuming of agent conversations,
particularly useful for human-in-the-loop scenarios where the
system waits for human input or approval.

Each pausable component implements PausableAgent protocol.
PauseManager coordinates pause/resume across multiple agents.
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Protocol, runtime_checkable

from .event_bus import Event, EventType, event_bus

logger = logging.getLogger(__name__)


class PauseState(Enum):
    RUNNING = "running"
    PAUSED = "paused"
    RESUMING = "resuming"


@dataclass
class PauseContext:
    """Context passed with pause/resume events."""

    reason: str = ""
    paused_by: str = ""
    paused_at: float = 0.0
    data: dict[str, Any] = field(default_factory=dict)


@runtime_checkable
class PausableAgent(Protocol):
    """Protocol for agents that support pause/resume."""

    @property
    def agent_name(self) -> str: ...

    def on_pause(self, context: PauseContext) -> None:
        """Called when the agent should pause. Save any in-flight state."""
        ...

    def on_resume(self, context: PauseContext) -> None:
        """Called when the agent should resume. Restore state if needed."""
        ...

    @property
    def pause_state(self) -> PauseState: ...


class SimplePausableAgent:
    """Reference implementation of PausableAgent."""

    def __init__(self, name: str):
        self._name = name
        self._state = PauseState.RUNNING
        self._pause_event = threading.Event()
        self._pause_event.set()  # starts unpaused
        self._saved_context: PauseContext | None = None

    @property
    def agent_name(self) -> str:
        return self._name

    @property
    def pause_state(self) -> PauseState:
        return self._state

    def on_pause(self, context: PauseContext) -> None:
        self._state = PauseState.PAUSED
        self._saved_context = context
        self._pause_event.clear()
        logger.info("Agent %s paused: %s", self._name, context.reason)

    def on_resume(self, context: PauseContext) -> None:
        self._state = PauseState.RESUMING
        self._pause_event.set()
        self._state = PauseState.RUNNING
        self._saved_context = None
        logger.info("Agent %s resumed", self._name)

    def wait_if_paused(self, timeout: float | None = None) -> bool:
        """Block until resumed. Returns True if resumed, False if timed out."""
        return self._pause_event.wait(timeout=timeout)

    @property
    def saved_context(self) -> PauseContext | None:
        return self._saved_context


class PauseManager:
    """Coordinates pause/resume across multiple agents.

    Usage:
        mgr = PauseManager()
        mgr.register(agent1)
        mgr.register(agent2)
        mgr.pause_all("Waiting for human approval")
        # ... human provides input ...
        mgr.resume_all()
    """

    def __init__(self):
        self._agents: dict[str, PausableAgent] = {}
        self._lock = threading.Lock()
        self._global_state = PauseState.RUNNING

    def register(self, agent: PausableAgent) -> None:
        with self._lock:
            self._agents[agent.agent_name] = agent

    def unregister(self, name: str) -> bool:
        with self._lock:
            return self._agents.pop(name, None) is not None

    def pause_all(self, reason: str = "", *, paused_by: str = "system") -> int:
        """Pause all registered agents. Returns count paused."""
        ctx = PauseContext(reason=reason, paused_by=paused_by, paused_at=time.time())
        count = 0
        with self._lock:
            self._global_state = PauseState.PAUSED
            for agent in self._agents.values():
                try:
                    agent.on_pause(ctx)
                    count += 1
                except Exception as exc:
                    logger.warning("Failed to pause %s: %s", agent.agent_name, exc)

        event_bus.emit(
            Event(
                type=EventType.AGENT_START,
                payload={"action": "pause_all", "reason": reason, "count": count},
                source="pause_manager",
            )
        )
        return count

    def resume_all(self, *, data: dict[str, Any] | None = None) -> int:
        """Resume all paused agents. Returns count resumed."""
        ctx = PauseContext(reason="resumed", data=data or {})
        count = 0
        with self._lock:
            self._global_state = PauseState.RUNNING
            for agent in self._agents.values():
                if agent.pause_state == PauseState.PAUSED:
                    try:
                        agent.on_resume(ctx)
                        count += 1
                    except Exception as exc:
                        logger.warning("Failed to resume %s: %s", agent.agent_name, exc)

        event_bus.emit(
            Event(
                type=EventType.AGENT_END,
                payload={"action": "resume_all", "count": count},
                source="pause_manager",
            )
        )
        return count

    def pause_agent(self, name: str, reason: str = "") -> bool:
        """Pause a single agent."""
        with self._lock:
            agent = self._agents.get(name)
        if agent is None:
            return False
        ctx = PauseContext(reason=reason, paused_by="system", paused_at=time.time())
        agent.on_pause(ctx)
        return True

    def resume_agent(self, name: str) -> bool:
        """Resume a single agent."""
        with self._lock:
            agent = self._agents.get(name)
        if agent is None:
            return False
        if agent.pause_state != PauseState.PAUSED:
            return False
        ctx = PauseContext(reason="resumed")
        agent.on_resume(ctx)
        return True

    def status(self) -> dict[str, Any]:
        with self._lock:
            agents_status = {name: agent.pause_state.value for name, agent in self._agents.items()}
        return {
            "global_state": self._global_state.value,
            "agent_count": len(agents_status),
            "agents": agents_status,
            "paused_count": sum(1 for s in agents_status.values() if s == "paused"),
        }

    @property
    def global_state(self) -> PauseState:
        return self._global_state
