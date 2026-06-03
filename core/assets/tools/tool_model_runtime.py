"""Model-hook coordination helpers for dynamic tool middleware."""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import Any

from .tool_call_runtime import ToolCallRuntime

logger = logging.getLogger(__name__)

_TOOL_MUTATION_NAMES = {"create_custom_tool", "create_agent", "remove_custom_tool"}


class ToolModelHookRuntime:
    """Coordinate before/after-model hooks around dynamic tool inventory."""

    def __init__(self, *, inventory: Any, control_runtime: Any):
        self._inventory = inventory
        self._control_runtime = control_runtime

    def before_model(self) -> None:
        try:
            mutation_notice = self._inventory.pop_mutation_notice()
            if mutation_notice:
                logger.info("Dynamic tool '%s' created", mutation_notice)

            dynamic_tools = self._inventory.list_dynamic_tools()
            self._inventory.refresh(dynamic_tools)

            if dynamic_tools:
                logger.debug("Loaded %d dynamic tools", len(dynamic_tools))
        except Exception as exc:
            logger.warning("Failed to load dynamic tools: %s", exc)
            self._inventory.fallback_to_base_tools()

    def inject_tools(self, request: Any) -> Any:
        request, added_count = self._inventory.inject_tools(request)
        if added_count:
            logger.debug("Injected %d dynamic tools into request", added_count)
        return request

    def wrap_model_call(self, request: Any, handler: Callable[[Any], Any]) -> Any:
        try:
            request = self.inject_tools(request)
        except Exception as exc:
            logger.warning("Failed to inject dynamic tools: %s", exc)
        return handler(request)

    async def wrap_model_call_async(
        self,
        request: Any,
        handler: Callable[[Any], Awaitable[Any]],
    ) -> Any:
        try:
            request = self.inject_tools(request)
        except Exception as exc:
            logger.warning("Failed to inject dynamic tools: %s", exc)
        return await handler(request)

    def after_model(self, state: Any) -> dict[str, Any] | None:
        messages = state.get("messages", [])
        if not messages:
            return None

        last_message = messages[-1]
        tool_calls = getattr(last_message, "tool_calls", []) or []
        approval_update = self._control_runtime.interrupt_for_pending_approvals(
            last_message=last_message,
            tool_calls=tool_calls,
            dynamic_tool_names=self._inventory.get_dynamic_tool_names(),
        )
        if approval_update is not None:
            return approval_update

        for tool_call in tool_calls:
            tool_name = ToolCallRuntime.tool_name(tool_call)
            logger.debug("Detected tool call: %s", tool_name)
        return None
