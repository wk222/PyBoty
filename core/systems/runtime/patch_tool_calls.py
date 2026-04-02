"""Middleware to patch dangling tool calls in the messages history.

Inspired by DeepAgents' PatchToolCallsMiddleware: when a new user message
arrives before a pending tool call completes, the AIMessage with
tool_calls has no matching ToolMessage.  Most LLM APIs reject this.

This middleware inserts synthetic cancellation ToolMessages so the
conversation stays valid.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

try:
    from langchain.agents.middleware import AgentMiddleware
    from langchain.agents.middleware.types import ModelRequest, ModelResponse
    from langchain_core.messages import AIMessage, ToolMessage

    _HAS_LC = True
except ImportError:
    _HAS_LC = False
    AgentMiddleware = object  # type: ignore[assignment,misc]
    ModelRequest = object  # type: ignore[assignment,misc]
    ModelResponse = object  # type: ignore[assignment,misc]


class PatchToolCallsMiddleware(AgentMiddleware if _HAS_LC else object):  # type: ignore[misc]
    """Fill in missing ToolMessages for dangling tool calls."""

    @property
    def name(self) -> str:
        return "PatchToolCallsMiddleware"

    @staticmethod
    def _patch(messages: list[Any]) -> list[Any] | None:
        if not messages:
            return None
        patched: list[Any] = []
        changed = False
        for i, msg in enumerate(messages):
            patched.append(msg)
            if not isinstance(msg, AIMessage) or not getattr(msg, "tool_calls", None):
                continue
            for tc in msg.tool_calls:
                tc_id = tc.get("id", "")
                has_response = any(
                    getattr(m, "type", None) == "tool" and getattr(m, "tool_call_id", None) == tc_id
                    for m in messages[i:]
                )
                if not has_response:
                    patched.append(
                        ToolMessage(
                            content=(
                                f"Tool call {tc.get('name', '?')} with id {tc_id} "
                                "was cancelled — another message came in before "
                                "it could be completed."
                            ),
                            name=tc.get("name", "unknown"),
                            tool_call_id=tc_id,
                        )
                    )
                    changed = True
        return patched if changed else None

    def wrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], ModelResponse],
    ) -> ModelResponse:
        patched = self._patch(list(request.messages))
        if patched is not None:
            request = request.override(messages=patched)
        return handler(request)

    async def awrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], Awaitable[ModelResponse]],
    ) -> ModelResponse:
        patched = self._patch(list(request.messages))
        if patched is not None:
            request = request.override(messages=patched)
        return await handler(request)
