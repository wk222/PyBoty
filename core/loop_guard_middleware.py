"""Loop detection and automatic model escalation middleware.

Detects when an agent repeatedly invokes the same tool with identical arguments,
indicating a behavioral loop.  On detection it:

1. Injects a corrective hint into the system prompt so the model switches strategy.
2. If the loop persists after the hint, records a flag that can be consumed by
   ``ChatModelWithFailover`` or upstream orchestration to escalate to a stronger
   model.

"""

from __future__ import annotations

import hashlib
import json
import logging
from collections import deque
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

try:
    from langchain.agents.middleware import AgentMiddleware
    from langchain.agents.middleware.types import ModelRequest, ModelResponse
    from langchain_core.messages import AIMessage

    _HAS_LC = True
except ImportError:
    _HAS_LC = False
    AgentMiddleware = object  # type: ignore[assignment,misc]
    ModelRequest = object  # type: ignore[assignment,misc]
    ModelResponse = object  # type: ignore[assignment,misc]

from .agent_prompt_middleware import append_to_system_message

logger = logging.getLogger(__name__)


LOOP_GUARD_IGNORED_TOOLS = frozenset({
    "write_todos",
    "compact_conversation",
    "list_tools",
    "tool_stats",
    "capability_bus",
})


def _tool_call_fingerprint(tc: dict[str, Any]) -> str | None:
    """Stable hash of tool-name + sorted args for dedup comparison.

    Returns None for infrastructure tools that should not participate
    in loop detection (e.g. write_todos is called between real work steps).
    """
    name = tc.get("name", "")
    if name in LOOP_GUARD_IGNORED_TOOLS:
        return None
    raw = json.dumps({"n": name, "a": tc.get("args", {})}, sort_keys=True)
    return hashlib.md5(raw.encode()).hexdigest()


@dataclass
class LoopGuardConfig:
    window_size: int = 8
    repeat_threshold: int = 3
    escalation_threshold: int = 5
    hint_cooldown_calls: int = 4


LOOP_HINT_PROMPT = (
    "\n\n## ⚠ Loop Detection Alert\n"
    "The system has detected you are repeating the same tool call with identical "
    "arguments ({repeat_count} times in the last {window} calls).  This is wasting "
    "tokens and not making progress.\n\n"
    "**Mandatory next steps:**\n"
    "1. Stop repeating the same action.\n"
    "2. Analyze WHY the previous attempts failed — read the error/result carefully.\n"
    "3. Try a fundamentally different approach (different tool, different arguments, "
    "or ask the user for clarification).\n"
    "4. If truly stuck, use `compact_conversation` or explain the blocker to the user.\n"
)


class LoopGuardMiddleware(AgentMiddleware if _HAS_LC else object):  # type: ignore[misc]
    """Detect and break agent behavioral loops."""

    def __init__(self, *, config: LoopGuardConfig | None = None):
        self._config = config or LoopGuardConfig()
        self._recent_fingerprints: deque[str] = deque(maxlen=self._config.window_size)
        self._hint_injected_at: int = -999
        self._total_calls: int = 0
        self._escalation_requested: bool = False
        self.stats = LoopGuardStats()

    @property
    def name(self) -> str:
        return "LoopGuardMiddleware"

    @property
    def should_escalate_model(self) -> bool:
        return self._escalation_requested

    def reset_escalation(self) -> None:
        self._escalation_requested = False

    def _scan_latest_tool_calls(self, messages: list[Any]) -> None:
        """Extract tool-call fingerprints from recent AI messages."""
        for msg in reversed(messages[-self._config.window_size * 2:]):
            if not isinstance(msg, AIMessage):
                continue
            for tc in getattr(msg, "tool_calls", None) or []:
                fp = _tool_call_fingerprint(tc)
                if fp is None:
                    continue
                if not self._recent_fingerprints or self._recent_fingerprints[-1] != fp:
                    self._recent_fingerprints.append(fp)

    def _detect_loop(self) -> tuple[bool, int]:
        """Return (is_loop, repeat_count) based on fingerprint window."""
        if len(self._recent_fingerprints) < self._config.repeat_threshold:
            return False, 0
        tail = list(self._recent_fingerprints)
        last = tail[-1]
        count = sum(1 for fp in tail if fp == last)
        return count >= self._config.repeat_threshold, count

    def _process_request(self, request: Any) -> Any:
        self._total_calls += 1
        messages = list(request.messages)
        self._scan_latest_tool_calls(messages)

        is_loop, repeat_count = self._detect_loop()
        if not is_loop:
            return request

        self.stats.loops_detected += 1
        logger.warning(
            "LoopGuard: detected %d repeated tool calls in window of %d",
            repeat_count,
            self._config.window_size,
        )

        calls_since_hint = self._total_calls - self._hint_injected_at
        if calls_since_hint <= self._config.hint_cooldown_calls:
            if repeat_count >= self._config.escalation_threshold:
                self._escalation_requested = True
                self.stats.escalations_triggered += 1
                logger.warning("LoopGuard: escalation requested after %d repeats", repeat_count)
            return request

        self._hint_injected_at = self._total_calls
        self.stats.hints_injected += 1

        hint = LOOP_HINT_PROMPT.format(
            repeat_count=repeat_count,
            window=self._config.window_size,
        )
        return request.override(
            system_message=append_to_system_message(request.system_message, hint),
        )

    def wrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], ModelResponse],
    ) -> ModelResponse:
        return handler(self._process_request(request))

    async def awrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], Awaitable[ModelResponse]],
    ) -> ModelResponse:
        return await handler(self._process_request(request))

    def get_stats(self) -> dict[str, Any]:
        return {
            "total_model_calls": self._total_calls,
            "loops_detected": self.stats.loops_detected,
            "hints_injected": self.stats.hints_injected,
            "escalations_triggered": self.stats.escalations_triggered,
            "escalation_pending": self._escalation_requested,
            "recent_fingerprints": list(self._recent_fingerprints),
        }


@dataclass
class LoopGuardStats:
    loops_detected: int = 0
    hints_injected: int = 0
    escalations_triggered: int = 0
