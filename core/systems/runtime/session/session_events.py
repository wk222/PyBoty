"""Session-scoped ephemeral event queue for prompt prefix injection.

Lightweight in-memory queue for human-readable events that should be
prefixed to the next prompt.  Events are session-scoped, deduped, and
auto-pruned.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field


@dataclass
class SessionEvent:
    """A single session-scoped event."""

    text: str
    ts: float = field(default_factory=time.time)
    context_key: str | None = None


MAX_EVENTS_PER_SESSION = 20


class SessionEventQueue:
    """Thread-safe per-session ephemeral event queue.

    Events are consumed when flushed (e.g. before building a prompt),
    ensuring each event is only shown once.
    """

    def __init__(self, max_events: int = MAX_EVENTS_PER_SESSION):
        self._lock = threading.Lock()
        self._queues: dict[str, list[SessionEvent]] = {}
        self._last_text: dict[str, str] = {}
        self._max_events = max_events

    def enqueue(self, session_key: str, text: str, *, context_key: str | None = None) -> None:
        """Add an event to a session's queue, deduplicating consecutive identical text."""
        with self._lock:
            if self._last_text.get(session_key) == text:
                return
            self._last_text[session_key] = text
            queue = self._queues.setdefault(session_key, [])
            queue.append(SessionEvent(text=text, context_key=context_key))
            if len(queue) > self._max_events:
                queue[:] = queue[-self._max_events :]

    def flush(self, session_key: str) -> list[SessionEvent]:
        """Drain and return all events for a session (consume-once)."""
        with self._lock:
            events = self._queues.pop(session_key, [])
            self._last_text.pop(session_key, None)
        return events

    def peek(self, session_key: str) -> list[SessionEvent]:
        """View events without consuming them."""
        with self._lock:
            return list(self._queues.get(session_key, []))

    def has_events(self, session_key: str) -> bool:
        with self._lock:
            return bool(self._queues.get(session_key))

    def clear(self, session_key: str) -> None:
        with self._lock:
            self._queues.pop(session_key, None)
            self._last_text.pop(session_key, None)

    def clear_all(self) -> None:
        with self._lock:
            self._queues.clear()
            self._last_text.clear()

    def session_count(self) -> int:
        with self._lock:
            return len(self._queues)

    def format_prompt_prefix(self, session_key: str) -> str:
        """Flush events and format them as a prompt prefix section."""
        events = self.flush(session_key)
        if not events:
            return ""
        lines = ["[System Events]"]
        for event in events:
            lines.append(f"- {event.text}")
        return "\n".join(lines)

    def prune_stale(self, max_age_seconds: float = 3600) -> int:
        """Remove events older than ``max_age_seconds`` across all sessions."""
        now = time.time()
        pruned = 0
        with self._lock:
            for session_key in list(self._queues.keys()):
                queue = self._queues[session_key]
                original_len = len(queue)
                queue[:] = [e for e in queue if now - e.ts <= max_age_seconds]
                pruned += original_len - len(queue)
                if not queue:
                    del self._queues[session_key]
                    self._last_text.pop(session_key, None)
        return pruned
