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


__all__ = [
    "on_message",
    "on_tool_call",
    "pybot_plugin",
]
