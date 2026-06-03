"""Session spine — the canonical run backbone for all PyBot capabilities.

This is Layer 0 (Root) of PyBot's architectural tree.  Every higher-layer
capability (governance, memory, tools, agents, apps, modes) ultimately
depends on the session spine for run tracking, event recording, compaction,
and context projection.

Public API is intentionally lazy-loaded to avoid circular imports at startup.
"""

from __future__ import annotations

from importlib import import_module
from typing import Any

_EXPORTS: dict[str, tuple[str, str]] = {
    # session_record
    "SessionRecord": (".session_record", "SessionRecord"),
    # session_runtime (composes all mixins)
    "SessionRuntime": (".session_runtime", "SessionRuntime"),
    # session_kernel
    "SessionKernel": (".session_kernel", "SessionKernel"),
    "SessionSidechain": (".session_kernel", "SessionSidechain"),
    # session_events
    "SessionEvent": (".session_events", "SessionEvent"),
    "SessionEventQueue": (".session_events", "SessionEventQueue"),
    # session_compaction
    "CompactionBoundary": (".session_compaction", "CompactionBoundary"),
    "create_compaction_boundary": (".session_compaction", "create_compaction_boundary"),
    # session_memory_policy
    "SessionMemoryDecision": (".session_memory_policy", "SessionMemoryDecision"),
    "validate_session_memory": (".session_memory_policy", "validate_session_memory"),
    "typed_memory_entry_payload": (".session_memory_policy", "typed_memory_entry_payload"),
    # session_engine
    "PyBotSessionEngine": (".session_engine", "PyBotSessionEngine"),
    "RunResult": (".session_engine", "RunResult"),
    "ModeTransition": (".session_engine", "ModeTransition"),
    "SessionStatus": (".session_engine", "SessionStatus"),
    # session_hygiene
    "SessionHygieneMixin": (".session_hygiene", "SessionHygieneMixin"),
    # session_sync
    "SessionSyncMixin": (".session_sync", "SessionSyncMixin"),
    # session_recorder
    "SessionRecorderMixin": (".session_recorder", "SessionRecorderMixin"),
    # session_applier
    "SessionApplierMixin": (".session_applier", "SessionApplierMixin"),
}

__all__ = sorted(_EXPORTS)


def __getattr__(name: str) -> Any:
    try:
        module_name, attr_name = _EXPORTS[name]
    except KeyError as exc:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}") from exc
    module = import_module(module_name, __name__)
    return getattr(module, attr_name)


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(__all__))
