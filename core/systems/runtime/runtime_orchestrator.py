"""Pure orchestration helpers extracted from :class:`core.PyBot`.

These helpers cover three small but recurring sub-flows that the root
runtime relies on:

* invoke the underlying LangChain agent with the standard config envelope;
* extract the best textual reply from the resulting message list;
* compute the display names of the active LangChain middleware stack;
* expose the most commonly used runtime services on an arbitrary holder
  object (the legacy ``_bind_runtime`` shim).

Splitting them out of ``agent.py`` lets each piece be unit-tested in
isolation without spinning up the full PyBot stack.
"""

from __future__ import annotations

from typing import Any, Callable, Iterable

DEFAULT_RECURSION_LIMIT = 100

NO_REPLY_PLACEHOLDER = "（无回复）"


def make_invoke_config(
    *,
    thread_id: str,
    recursion_limit: int = DEFAULT_RECURSION_LIMIT,
) -> dict[str, Any]:
    """Standard ``LangGraph`` invoke config envelope used by P yBot."""
    return {
        "configurable": {"thread_id": thread_id},
        "recursion_limit": recursion_limit,
    }


def invoke_agent(agent: Any, message: str, *, config: dict[str, Any]) -> dict[str, Any]:
    """Send a single user message through ``agent.invoke``."""
    return agent.invoke(
        {"messages": [{"role": "user", "content": message}]},
        config=config,
    )


def extract_final_reply(
    messages: Iterable[Any],
    *,
    deduplicate: Callable[[str], str] | None = None,
) -> str:
    """Pick the best textual reply from a finished agent message list.

    Prefers the most recent non-empty ``AIMessage``; falls back to the
    most recent ``ToolMessage`` when no AI text is available.
    """
    from langchain_core.messages import AIMessage, ToolMessage

    last_tool_content: str | None = None
    for msg in reversed(list(messages)):
        if isinstance(msg, AIMessage):
            text = (msg.content or "").strip()
            if text:
                return deduplicate(text) if deduplicate else text
        elif isinstance(msg, ToolMessage) and last_tool_content is None:
            text = (msg.content or "").strip()
            if text:
                last_tool_content = text
    if last_tool_content:
        return last_tool_content
    return NO_REPLY_PLACEHOLDER


def bind_runtime_shortcuts(holder: Any, runtime: Any) -> None:
    """Expose the most commonly used runtime services on ``holder``.

    This is the canonical replacement for the old ``_bind_runtime``
    method and intentionally only mirrors the four shortcuts that
    external scripts and tests historically relied on.
    """
    holder.workspace = runtime.workspace
    holder.memory = runtime.memory
    holder.capability_bus = runtime.capability_bus
    holder.llm = runtime.llm


def collect_lc_middleware_names(runtime: Any) -> list[str]:
    """Return the display names of the active LangChain middleware stack.

    Falls back to the textual layer names tracked by the runtime's
    middleware stack when the root middleware factory is not available
    (e.g. early bootstrap or partial test fixtures).
    """
    try:
        from core.systems.middleware.agent_middleware_factory import (
            build_root_langchain_middleware,
        )

        mws = build_root_langchain_middleware(runtime=runtime)
    except Exception:
        return getattr(runtime.middleware_stack, "layers", [])
    return [getattr(m, "name", None) or type(m).__name__ for m in mws]


__all__ = [
    "DEFAULT_RECURSION_LIMIT",
    "NO_REPLY_PLACEHOLDER",
    "bind_runtime_shortcuts",
    "collect_lc_middleware_names",
    "extract_final_reply",
    "invoke_agent",
    "make_invoke_config",
]
