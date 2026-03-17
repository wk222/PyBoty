"""Global publish-subscribe event bus.

All modules (tools, approval, memory, MCP, cost, workflow) emit events
through a single bus. Handlers can be sync or async, are priority-ordered,
and are isolated — one handler blowing up won't affect the rest.
"""

from __future__ import annotations

import asyncio
import enum
import logging
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


class EventType(str, enum.Enum):
    TOOL_CALL = "tool_call"
    TOOL_RESULT = "tool_result"
    AGENT_START = "agent_start"
    AGENT_END = "agent_end"
    APPROVAL_REQUEST = "approval_request"
    APPROVAL_RESOLVED = "approval_resolved"
    MEMORY_WRITE = "memory_write"
    MEMORY_SEARCH = "memory_search"
    MCP_CONNECT = "mcp_connect"
    MCP_DISCONNECT = "mcp_disconnect"
    COST_RECORD = "cost_record"
    WORKFLOW_STEP = "workflow_step"
    GUARDRAIL_PASS = "guardrail_pass"
    GUARDRAIL_FAIL = "guardrail_fail"
    GUARDRAIL_RETRY = "guardrail_retry"
    ERROR = "error"


@dataclass
class Event:
    type: EventType
    payload: dict[str, Any] = field(default_factory=dict)
    source: str = ""
    session_id: str | None = None
    timestamp: float = field(default_factory=time.time)


@dataclass
class _Subscription:
    handler: Callable
    priority: int
    is_async: bool


_DEFAULT_HISTORY_LIMIT = 500


class EventBus:
    """Thread-safe publish-subscribe event bus with priority ordering."""

    def __init__(self, history_limit: int = _DEFAULT_HISTORY_LIMIT):
        self._lock = threading.Lock()
        self._subs: dict[EventType, list[_Subscription]] = {}
        self._history: list[Event] = []
        self._history_limit = history_limit

    def subscribe(
        self,
        event_type: EventType,
        handler: Callable,
        *,
        priority: int = 0,
    ) -> None:
        is_async = asyncio.iscoroutinefunction(handler)
        sub = _Subscription(handler=handler, priority=priority, is_async=is_async)
        with self._lock:
            subs = self._subs.setdefault(event_type, [])
            subs.append(sub)
            subs.sort(key=lambda s: -s.priority)

    def unsubscribe(self, event_type: EventType, handler: Callable) -> bool:
        with self._lock:
            subs = self._subs.get(event_type, [])
            before = len(subs)
            self._subs[event_type] = [s for s in subs if s.handler is not handler]
            return len(self._subs[event_type]) < before

    def emit(self, event: Event) -> None:
        with self._lock:
            subs = list(self._subs.get(event.type, []))
            self._history.append(event)
            if len(self._history) > self._history_limit:
                self._history = self._history[-self._history_limit :]

        for sub in subs:
            try:
                if sub.is_async:
                    try:
                        loop = asyncio.get_running_loop()
                    except RuntimeError:
                        loop = None
                    if loop and loop.is_running():
                        loop.create_task(sub.handler(event))
                    else:
                        asyncio.run(sub.handler(event))
                else:
                    sub.handler(event)
            except Exception:
                logger.exception("EventBus handler %r failed for %s", sub.handler, event.type)

    async def emit_async(self, event: Event) -> None:
        with self._lock:
            subs = list(self._subs.get(event.type, []))
            self._history.append(event)
            if len(self._history) > self._history_limit:
                self._history = self._history[-self._history_limit :]

        for sub in subs:
            try:
                if sub.is_async:
                    await sub.handler(event)
                else:
                    sub.handler(event)
            except Exception:
                logger.exception("EventBus handler %r failed for %s", sub.handler, event.type)

    def history(
        self,
        event_type: EventType | None = None,
        *,
        limit: int = 100,
    ) -> list[Event]:
        with self._lock:
            events = self._history
            if event_type is not None:
                events = [e for e in events if e.type == event_type]
            return events[-limit:]

    def clear(self) -> None:
        with self._lock:
            self._subs.clear()
            self._history.clear()

    def subscriber_count(self, event_type: EventType | None = None) -> int:
        with self._lock:
            if event_type is not None:
                return len(self._subs.get(event_type, []))
            return sum(len(s) for s in self._subs.values())


event_bus = EventBus()
