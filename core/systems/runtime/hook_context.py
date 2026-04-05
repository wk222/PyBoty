"""Structured hook context system — typed lifecycle hooks with directory discovery.

Provides a structured, typed hook layer on top of the existing EventBus:

  - **HookType**: categorises lifecycle events (message, agent, session, etc.)
  - **HookContext**: strongly-typed context objects for each hook type
  - **HookHandler**: a registered handler with priority and metadata
  - **HookRegistry**: directory-based discovery + registration
  - **HookRunner**: executes registered hooks with error isolation

The EventBus handles raw pub/sub.  The Hook system adds **typed contexts**,
**directory discovery**, and **handler metadata** on top.

Usage::

    from core.systems.runtime.hook_context import HookType, MessageReceivedContext, hook_registry

    @hook_registry.on(HookType.MESSAGE_RECEIVED)
    def my_hook(ctx: MessageReceivedContext) -> None:
        if "secret" in ctx.content:
            ctx.cancel = True

    hook_registry.run(HookType.MESSAGE_RECEIVED, MessageReceivedContext(
        content="hello", channel="web", sender_id="user1",
    ))
"""

from __future__ import annotations

import importlib.util
import logging
import os
from collections import defaultdict
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable

logger = logging.getLogger(__name__)


class HookType(str, Enum):
    MESSAGE_RECEIVED = "message.received"
    MESSAGE_SENDING = "message.sending"
    AGENT_BOOTSTRAP = "agent.bootstrap"
    AGENT_BEFORE_TURN = "agent.before_turn"
    AGENT_AFTER_TURN = "agent.after_turn"
    AGENT_END = "agent.end"
    SESSION_START = "session.start"
    SESSION_END = "session.end"
    TOOL_BEFORE_CALL = "tool.before_call"
    TOOL_AFTER_CALL = "tool.after_call"
    WORKFLOW_BEFORE_NODE = "workflow.before_node"
    WORKFLOW_AFTER_NODE = "workflow.after_node"


@dataclass
class BaseHookContext:
    """Base context with cancel support and metadata."""
    cancel: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)

    def set_cancel(self, reason: str = "") -> None:
        self.cancel = True
        if reason:
            self.metadata["cancel_reason"] = reason


@dataclass
class MessageReceivedContext(BaseHookContext):
    """Context for MESSAGE_RECEIVED hooks."""
    content: str = ""
    channel: str = ""
    sender_id: str = ""
    thread_id: str | None = None
    attachments: list[str] = field(default_factory=list)


@dataclass
class MessageSendingContext(BaseHookContext):
    """Context for MESSAGE_SENDING hooks — can modify content before send."""
    content: str = ""
    channel: str = ""
    recipient_id: str = ""


@dataclass
class AgentBootstrapContext(BaseHookContext):
    """Context for AGENT_BOOTSTRAP hooks — modify agent config before start."""
    agent_name: str = ""
    config: dict[str, Any] = field(default_factory=dict)
    tools: list[str] = field(default_factory=list)
    system_prompt: str = ""


@dataclass
class AgentTurnContext(BaseHookContext):
    """Context for BEFORE/AFTER turn hooks."""
    agent_name: str = ""
    messages: list[Any] = field(default_factory=list)
    turn_number: int = 0
    response: str = ""


@dataclass
class ToolCallContext(BaseHookContext):
    """Context for TOOL_BEFORE/AFTER_CALL hooks."""
    tool_name: str = ""
    arguments: dict[str, Any] = field(default_factory=dict)
    result: str = ""
    duration: float = 0.0


@dataclass
class WorkflowNodeContext(BaseHookContext):
    """Context for WORKFLOW_BEFORE/AFTER_NODE hooks."""
    workflow_id: str = ""
    node_id: str = ""
    node_type: str = ""
    config: dict[str, Any] = field(default_factory=dict)
    result: Any = None


@dataclass
class SessionContext(BaseHookContext):
    """Context for SESSION_START/END hooks."""
    session_id: str = ""
    channel: str = ""
    agent_name: str = ""


@dataclass
class _HookHandler:
    handler: Callable
    priority: int = 0
    name: str = ""
    source: str = ""


class HookRegistry:
    """Central registry for lifecycle hooks with directory discovery."""

    def __init__(self) -> None:
        self._handlers: dict[HookType, list[_HookHandler]] = {}

    def register(
        self,
        hook_type: HookType,
        handler: Callable,
        *,
        priority: int = 0,
        name: str = "",
        source: str = "code",
    ) -> None:
        entry = _HookHandler(handler=handler, priority=priority, name=name or handler.__name__, source=source)
        handlers = self._get_handlers(hook_type)
        handlers.append(entry)
        handlers.sort(key=lambda h: -h.priority)

    def on(self, hook_type: HookType, *, priority: int = 0) -> Callable:
        """Decorator to register a hook handler."""
        def decorator(fn: Callable) -> Callable:
            self.register(hook_type, fn, priority=priority, name=fn.__name__, source="decorator")
            return fn
        return decorator

    def unregister(self, hook_type: HookType, handler: Callable) -> bool:
        handlers = self._get_handlers(hook_type)
        before = len(handlers)
        self._handlers[hook_type] = [h for h in handlers if h.handler is not handler]
        return len(self._handlers.get(hook_type, [])) < before

    def run(self, hook_type: HookType, context: BaseHookContext) -> BaseHookContext:
        """Execute all handlers for *hook_type* with *context*, in priority order.

        If any handler sets ``context.cancel = True``, subsequent handlers
        still run (they can check ``context.cancel``), but the caller
        should respect the cancellation.
        """
        for entry in self._handlers.get(hook_type, []):
            try:
                entry.handler(context)
            except Exception:
                logger.exception("Hook handler %r failed for %s", entry.name, hook_type.value)
        return context

    def _get_handlers(self, hook_type: HookType) -> list[_HookHandler]:
        if hook_type not in self._handlers:
            self._handlers[hook_type] = []
        return self._handlers[hook_type]

    def handler_count(self, hook_type: HookType | None = None) -> int:
        if hook_type is not None:
            return len(self._handlers.get(hook_type, []))
        return sum(len(hs) for hs in self._handlers.values())

    def list_handlers(self, hook_type: HookType) -> list[dict[str, Any]]:
        return [
            {"name": h.name, "priority": h.priority, "source": h.source}
            for h in self._handlers.get(hook_type, [])
        ]

    def discover(self, directories: list[str]) -> int:
        """Scan directories for hook modules and register handlers.

        Each directory may contain Python files with a ``register_hooks(registry)``
        function that receives this HookRegistry instance.
        """
        count = 0
        for dir_path in directories:
            if not os.path.isdir(dir_path):
                continue
            for filename in os.listdir(dir_path):
                if not filename.endswith(".py") or filename.startswith("_"):
                    continue
                module_path = os.path.join(dir_path, filename)
                try:
                    spec = importlib.util.spec_from_file_location(filename[:-3], module_path)
                    if spec and spec.loader:
                        mod = importlib.util.module_from_spec(spec)
                        spec.loader.exec_module(mod)
                        if hasattr(mod, "register_hooks"):
                            before = self.handler_count()
                            mod.register_hooks(self)
                            added = self.handler_count() - before
                            count += added
                            logger.info("Loaded %d hooks from %s", added, filename)
                except Exception:
                    logger.exception("Failed to load hook module: %s", module_path)
        return count

    def clear(self) -> None:
        self._handlers.clear()


_global_hook_registry: HookRegistry | None = None


def get_hook_registry() -> HookRegistry:
    global _global_hook_registry
    if _global_hook_registry is None:
        _global_hook_registry = HookRegistry()
    return _global_hook_registry

hook_registry = get_hook_registry()
