"""Composable termination conditions for agent/workflow execution.

Inspired by AutoGen's rich termination system. Conditions can be
combined with AND/OR logic and attached to workflow nodes or agents.
"""

from __future__ import annotations

import re
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable


@dataclass
class TerminationContext:
    """Snapshot of execution state for condition evaluation."""

    messages_count: int = 0
    token_usage: int = 0
    elapsed_seconds: float = 0.0
    last_output: str = ""
    custom_data: dict[str, Any] = field(default_factory=dict)


@runtime_checkable
class TerminationCondition(Protocol):
    """Protocol for termination conditions."""

    def check(self, context: TerminationContext) -> bool:
        """Return True if the condition is met (should terminate)."""
        ...

    @property
    def reason(self) -> str:
        """Human-readable description of why termination triggered."""
        ...


class MaxMessages:
    def __init__(self, limit: int):
        self._limit = limit

    def check(self, context: TerminationContext) -> bool:
        return context.messages_count >= self._limit

    @property
    def reason(self) -> str:
        return f"Max messages reached ({self._limit})"


class MaxTokens:
    def __init__(self, limit: int):
        self._limit = limit

    def check(self, context: TerminationContext) -> bool:
        return context.token_usage >= self._limit

    @property
    def reason(self) -> str:
        return f"Max tokens reached ({self._limit})"


class Timeout:
    def __init__(self, seconds: float):
        self._seconds = seconds

    def check(self, context: TerminationContext) -> bool:
        return context.elapsed_seconds >= self._seconds

    @property
    def reason(self) -> str:
        return f"Timeout after {self._seconds}s"


class TextMatch:
    """Terminates when last_output matches a regex pattern."""

    def __init__(self, pattern: str, *, flags: int = 0):
        self._pattern = re.compile(pattern, flags)
        self._raw = pattern

    def check(self, context: TerminationContext) -> bool:
        return bool(self._pattern.search(context.last_output))

    @property
    def reason(self) -> str:
        return f"Text matched: {self._raw}"


class ScoreThreshold:
    """Terminates when a named score in custom_data meets/exceeds a threshold."""

    def __init__(self, key: str, threshold: float):
        self._key = key
        self._threshold = threshold

    def check(self, context: TerminationContext) -> bool:
        val = context.custom_data.get(self._key, 0.0)
        try:
            return float(val) >= self._threshold
        except (ValueError, TypeError):
            return False

    @property
    def reason(self) -> str:
        return f"Score '{self._key}' >= {self._threshold}"


class ExternalSignal:
    """Terminates when an external threading.Event is set."""

    def __init__(self, event: threading.Event | None = None):
        self._event = event or threading.Event()

    def signal(self) -> None:
        self._event.set()

    def reset(self) -> None:
        self._event.clear()

    def check(self, context: TerminationContext) -> bool:
        return self._event.is_set()

    @property
    def reason(self) -> str:
        return "External signal received"


class FunctionalCondition:
    """Terminates based on a custom callable."""

    def __init__(self, fn: Callable[[TerminationContext], bool], description: str = "custom"):
        self._fn = fn
        self._desc = description

    def check(self, context: TerminationContext) -> bool:
        return self._fn(context)

    @property
    def reason(self) -> str:
        return f"Functional condition: {self._desc}"


class AnyCondition:
    """Terminates if ANY of the child conditions is met (OR logic)."""

    def __init__(self, *conditions: TerminationCondition):
        self._conditions = list(conditions)
        self._triggered: TerminationCondition | None = None

    def check(self, context: TerminationContext) -> bool:
        for c in self._conditions:
            if c.check(context):
                self._triggered = c
                return True
        self._triggered = None
        return False

    @property
    def reason(self) -> str:
        if self._triggered:
            return f"Any: {self._triggered.reason}"
        return "Any: none triggered"


class AllConditions:
    """Terminates only if ALL of the child conditions are met (AND logic)."""

    def __init__(self, *conditions: TerminationCondition):
        self._conditions = list(conditions)

    def check(self, context: TerminationContext) -> bool:
        return all(c.check(context) for c in self._conditions)

    @property
    def reason(self) -> str:
        reasons = [c.reason for c in self._conditions]
        return f"All: [{', '.join(reasons)}]"
