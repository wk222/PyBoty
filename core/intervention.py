"""Intervention handlers for intercepting and modifying agent actions.

Unlike EventBus (post-hoc notifications), intervention handlers run
*before* an action executes and can modify or drop it entirely.
Inspired by AutoGen's InterventionHandler pattern.
"""

from __future__ import annotations

import logging
import re
import threading
import time
from dataclasses import dataclass
from enum import Enum
from typing import Any, Protocol, runtime_checkable

logger = logging.getLogger(__name__)


class InterventionResult(Enum):
    PASS = "pass"
    MODIFY = "modify"
    DROP = "drop"


@dataclass
class InterventionResponse:
    result: InterventionResult
    modified_content: Any | None = None
    reason: str = ""

    @staticmethod
    def allow() -> InterventionResponse:
        return InterventionResponse(result=InterventionResult.PASS)

    @staticmethod
    def modify(content: Any, reason: str = "") -> InterventionResponse:
        return InterventionResponse(result=InterventionResult.MODIFY, modified_content=content, reason=reason)

    @staticmethod
    def drop(reason: str = "") -> InterventionResponse:
        return InterventionResponse(result=InterventionResult.DROP, reason=reason)


@runtime_checkable
class InterventionHandler(Protocol):
    """Protocol for intervention handlers."""

    def on_tool_call(self, tool_name: str, args: dict[str, Any]) -> InterventionResponse: ...
    def on_agent_message(self, agent_name: str, message: str) -> InterventionResponse: ...
    def on_delegation(self, from_agent: str, to_agent: str, task: str) -> InterventionResponse: ...


class ContentFilterHandler:
    """Blocks messages matching any of the given regex patterns."""

    def __init__(self, blocked_patterns: list[str]):
        self._patterns = [re.compile(p, re.IGNORECASE) for p in blocked_patterns]

    def on_tool_call(self, tool_name: str, args: dict[str, Any]) -> InterventionResponse:
        args_str = str(args)
        for p in self._patterns:
            if p.search(args_str):
                return InterventionResponse.drop(f"Blocked by content filter: {p.pattern}")
        return InterventionResponse.allow()

    def on_agent_message(self, agent_name: str, message: str) -> InterventionResponse:
        for p in self._patterns:
            if p.search(message):
                return InterventionResponse.drop(f"Blocked by content filter: {p.pattern}")
        return InterventionResponse.allow()

    def on_delegation(self, from_agent: str, to_agent: str, task: str) -> InterventionResponse:
        for p in self._patterns:
            if p.search(task):
                return InterventionResponse.drop(f"Blocked by content filter: {p.pattern}")
        return InterventionResponse.allow()


class RateLimitHandler:
    """Limits the number of calls per minute."""

    def __init__(self, max_calls_per_minute: int):
        self._max = max_calls_per_minute
        self._window = 60.0
        self._calls: list[float] = []
        self._lock = threading.Lock()

    def _check_rate(self) -> InterventionResponse:
        now = time.time()
        with self._lock:
            self._calls = [t for t in self._calls if now - t < self._window]
            if len(self._calls) >= self._max:
                return InterventionResponse.drop(f"Rate limit exceeded: {self._max} calls/min")
            self._calls.append(now)
        return InterventionResponse.allow()

    def on_tool_call(self, tool_name: str, args: dict[str, Any]) -> InterventionResponse:
        return self._check_rate()

    def on_agent_message(self, agent_name: str, message: str) -> InterventionResponse:
        return self._check_rate()

    def on_delegation(self, from_agent: str, to_agent: str, task: str) -> InterventionResponse:
        return self._check_rate()


class LoggingHandler:
    """Logs all interventions without modifying them."""

    def __init__(self):
        self.log: list[dict[str, Any]] = []

    def on_tool_call(self, tool_name: str, args: dict[str, Any]) -> InterventionResponse:
        entry = {"type": "tool_call", "tool": tool_name, "args": args, "time": time.time()}
        self.log.append(entry)
        logger.debug("Intervention log: tool_call %s", tool_name)
        return InterventionResponse.allow()

    def on_agent_message(self, agent_name: str, message: str) -> InterventionResponse:
        entry = {"type": "agent_message", "agent": agent_name, "message_preview": message[:100], "time": time.time()}
        self.log.append(entry)
        logger.debug("Intervention log: agent_message from %s", agent_name)
        return InterventionResponse.allow()

    def on_delegation(self, from_agent: str, to_agent: str, task: str) -> InterventionResponse:
        entry = {
            "type": "delegation",
            "from": from_agent,
            "to": to_agent,
            "task_preview": task[:100],
            "time": time.time(),
        }
        self.log.append(entry)
        logger.debug("Intervention log: delegation %s -> %s", from_agent, to_agent)
        return InterventionResponse.allow()


class InterventionChain:
    """Runs multiple handlers in sequence.

    - If any handler returns DROP, the chain stops and returns DROP.
    - MODIFY results are accumulated: the modified content is passed
      to subsequent handlers (for tool_call, modified args; for
      agent_message, modified message text).
    - PASS continues to the next handler.
    """

    def __init__(self, handlers: list[InterventionHandler] | None = None):
        self._handlers: list[InterventionHandler] = list(handlers or [])

    def add(self, handler: InterventionHandler) -> None:
        self._handlers.append(handler)

    def on_tool_call(self, tool_name: str, args: dict[str, Any]) -> InterventionResponse:
        current_args = args
        final_response = InterventionResponse.allow()
        for h in self._handlers:
            resp = h.on_tool_call(tool_name, current_args)
            if resp.result == InterventionResult.DROP:
                return resp
            if resp.result == InterventionResult.MODIFY and resp.modified_content is not None:
                current_args = resp.modified_content
                final_response = InterventionResponse.modify(current_args, resp.reason)
        return final_response

    def on_agent_message(self, agent_name: str, message: str) -> InterventionResponse:
        current_msg = message
        final_response = InterventionResponse.allow()
        for h in self._handlers:
            resp = h.on_agent_message(agent_name, current_msg)
            if resp.result == InterventionResult.DROP:
                return resp
            if resp.result == InterventionResult.MODIFY and resp.modified_content is not None:
                current_msg = resp.modified_content
                final_response = InterventionResponse.modify(current_msg, resp.reason)
        return final_response

    def on_delegation(self, from_agent: str, to_agent: str, task: str) -> InterventionResponse:
        current_task = task
        final_response = InterventionResponse.allow()
        for h in self._handlers:
            resp = h.on_delegation(from_agent, to_agent, current_task)
            if resp.result == InterventionResult.DROP:
                return resp
            if resp.result == InterventionResult.MODIFY and resp.modified_content is not None:
                current_task = resp.modified_content
                final_response = InterventionResponse.modify(current_task, resp.reason)
        return final_response
