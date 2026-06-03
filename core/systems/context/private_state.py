"""Private state attribute registry for agent state isolation.

Inspired by DeepAgents' ``PrivateStateAttr`` — provides a declarative way
for middleware to mark state keys as private, ensuring they are automatically
excluded from subagent state propagation and invoke output.

Usage::

    from core.systems.context.private_state import register_private_keys, PRIVATE_STATE_KEYS

    register_private_keys("TodoListMiddleware", {"todos", "_todo_state"})
    register_private_keys("SkillsMiddleware", {"skills_metadata"})

    # In subagent runtime:
    filtered = {k: v for k, v in state.items() if k not in PRIVATE_STATE_KEYS}
"""

from __future__ import annotations

import threading

_lock = threading.Lock()
_registry: dict[str, frozenset[str]] = {}

BUILTIN_PRIVATE_KEYS = frozenset(
    {
        "messages",
        "todos",
        "structured_response",
        "skills_metadata",
        "memory_contents",
        "_summarization_event",
        "_todo_state",
    }
)


def register_private_keys(owner: str, keys: set[str] | frozenset[str]) -> None:
    """Register state keys as private for a given middleware/component."""
    with _lock:
        existing = _registry.get(owner, frozenset())
        _registry[owner] = existing | frozenset(keys)


def get_private_keys() -> frozenset[str]:
    """Return the full set of private state keys from all registrations."""
    with _lock:
        combined = set(BUILTIN_PRIVATE_KEYS)
        for keys in _registry.values():
            combined |= keys
        return frozenset(combined)


def get_private_keys_by_owner() -> dict[str, frozenset[str]]:
    """Return private keys grouped by registering owner."""
    with _lock:
        result = {"builtin": BUILTIN_PRIVATE_KEYS}
        result.update(_registry)
        return result


PRIVATE_STATE_KEYS = property(lambda self: get_private_keys())


register_private_keys("TodoListMiddleware", {"todos", "_todo_state"})
register_private_keys("SummarizationMiddleware", {"_summarization_event"})
register_private_keys("MemoryMiddleware", {"memory_contents"})
register_private_keys("SkillsMiddleware", {"skills_metadata"})
