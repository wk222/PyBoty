"""Pluggable context window strategies for conversation management.

Inspired by AutoGen's multiple context implementations:
  - BufferedChatContext: sliding window (keep last N messages)
  - TokenLimitedChatContext: fit within a token budget
  - HeadAndTailChatContext: keep system/early messages + recent tail

Each strategy implements the ContextStrategy protocol and can be
composed or swapped transparently.
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from .context_manager import count_message_tokens, count_messages_tokens


@runtime_checkable
class ContextStrategy(Protocol):
    """Protocol for context window strategies."""

    def apply(self, messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Return a trimmed message list."""
        ...


class BufferedChatContext:
    """Keep the last `buffer_size` non-system messages, always preserve system."""

    def __init__(self, buffer_size: int = 20):
        self._buffer_size = buffer_size

    def apply(self, messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        system = [m for m in messages if m.get("role") == "system"]
        non_system = [m for m in messages if m.get("role") != "system"]
        if len(non_system) <= self._buffer_size:
            return messages
        return system + non_system[-self._buffer_size :]


class TokenLimitedChatContext:
    """Keep as many recent messages as fit within `max_tokens`.

    System messages are always included (their tokens are subtracted
    from the budget first).
    """

    def __init__(self, max_tokens: int = 8000):
        self._max_tokens = max_tokens

    def apply(self, messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        total = count_messages_tokens(messages)
        if total <= self._max_tokens:
            return messages

        system = [m for m in messages if m.get("role") == "system"]
        non_system = [m for m in messages if m.get("role") != "system"]

        sys_tokens = sum(count_message_tokens(m) for m in system)
        budget = self._max_tokens - sys_tokens

        if budget <= 0:
            return system

        kept: list[dict[str, Any]] = []
        used = 0
        for msg in reversed(non_system):
            t = count_message_tokens(msg)
            if used + t > budget:
                break
            kept.insert(0, msg)
            used += t

        return system + kept


class HeadAndTailChatContext:
    """Keep `head_count` earliest non-system messages and `tail_count` latest.

    Useful for preserving the initial task description + recent context.
    System messages are always kept.
    """

    def __init__(self, head_count: int = 3, tail_count: int = 10):
        self._head = head_count
        self._tail = tail_count

    def apply(self, messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        system = [m for m in messages if m.get("role") == "system"]
        non_system = [m for m in messages if m.get("role") != "system"]

        total_keep = self._head + self._tail
        if len(non_system) <= total_keep:
            return messages

        head = non_system[: self._head]
        tail = non_system[-self._tail :]

        omitted = len(non_system) - total_keep
        separator = {
            "role": "system",
            "content": f"[... 省略了 {omitted} 条中间消息 ...]",
        }
        return system + head + [separator] + tail


class CompositeContextStrategy:
    """Apply multiple strategies in sequence (pipeline)."""

    def __init__(self, strategies: list[ContextStrategy]):
        self._strategies = strategies

    def apply(self, messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        result = messages
        for strategy in self._strategies:
            result = strategy.apply(result)
        return result
