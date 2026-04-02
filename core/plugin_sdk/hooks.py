"""Public hook context types for PyBot plugins."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class PluginHookContext:
    plugin_id: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)
    cancel: bool = False
    cancel_reason: str = ""

    def block(self, reason: str) -> None:
        self.cancel = True
        self.cancel_reason = reason


@dataclass
class ToolCallHookContext(PluginHookContext):
    tool_name: str = ""
    arguments: dict[str, Any] = field(default_factory=dict)
    tool_call_id: str = ""
    result: Any = None
    duration: float = 0.0
    phase: str = "before"


@dataclass
class MessageHookContext(PluginHookContext):
    content: str = ""
    channel: str = "chat"
    sender_id: str = "user"
    thread_id: str | None = None


__all__ = [
    "MessageHookContext",
    "PluginHookContext",
    "ToolCallHookContext",
]
