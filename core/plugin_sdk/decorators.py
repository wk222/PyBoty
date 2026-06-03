"""Decorators for declaring PyBot plugins and lifecycle hooks."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any


def pybot_plugin(*, id: str | None = None, name: str | None = None) -> Callable[[type], type]:
    def decorator(cls: type) -> type:
        cls._pybot_plugin_meta = {  # type: ignore[attr-defined]
            "id": id,
            "name": name,
        }
        return cls

    return decorator


def on_tool_call(*, when: str = "before") -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    normalized = str(when).strip().lower() or "before"
    if normalized not in {"before", "after"}:
        raise ValueError("on_tool_call only supports when='before' or when='after'")

    def decorator(fn: Callable[..., Any]) -> Callable[..., Any]:
        fn._pybot_tool_hook = normalized  # type: ignore[attr-defined]
        return fn

    return decorator


def on_message(fn: Callable[..., Any]) -> Callable[..., Any]:
    fn._pybot_message_hook = True  # type: ignore[attr-defined]
    return fn


def on_startup(fn: Callable[..., Any]) -> Callable[..., Any]:
    """Mark a method as a startup hook (called once when the host comes up)."""
    fn._pybot_lifecycle_hook = "on_startup"  # type: ignore[attr-defined]
    return fn


def on_shutdown(fn: Callable[..., Any]) -> Callable[..., Any]:
    """Mark a method as a shutdown hook (called once when the host stops)."""
    fn._pybot_lifecycle_hook = "on_shutdown"  # type: ignore[attr-defined]
    return fn


def on_settings_change(fn: Callable[..., Any]) -> Callable[..., Any]:
    """Mark a method as a settings-change hook.

    The host invokes the hook with ``(plugin_id, new_settings_dict)``
    whenever the user (or another plugin) writes
    ``workspace/extensions/<plugin_id>/settings.json``.
    """
    fn._pybot_lifecycle_hook = "on_settings_change"  # type: ignore[attr-defined]
    return fn


def on_task_heartbeat(fn: Callable[..., Any]) -> Callable[..., Any]:
    """Mark a method as a task-heartbeat hook.

    Receives the :class:`~core.systems.tasks.long_running_task.TaskSnapshot`
    payload of every ``task.heartbeat`` event.  Useful for plugins that
    react to long-running progress (e.g. push-notify on stalled jobs).
    """
    fn._pybot_lifecycle_hook = "on_task_heartbeat"  # type: ignore[attr-defined]
    return fn


__all__ = [
    "on_message",
    "on_shutdown",
    "on_settings_change",
    "on_startup",
    "on_task_heartbeat",
    "on_tool_call",
    "pybot_plugin",
]
