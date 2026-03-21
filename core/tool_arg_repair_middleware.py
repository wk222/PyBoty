"""Pluggable LangChain middleware that auto-repairs tool arguments before execution.

Wraps the stateless ``repair_tool_args`` logic from ``tool_arg_repair`` into a
standard ``AgentMiddleware``. When placed in the middleware stack it intercepts
``wrap_tool_call`` / ``awrap_tool_call`` and fixes common LLM type mismatches
(list→str, str→int, broken JS regex, etc.) *before* Pydantic validation runs.

The middleware is self-contained and can be enabled/disabled independently.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import Any

from langchain.tools import BaseTool
from langchain_core.messages import ToolMessage
from langgraph.types import Command

try:
    from langchain.agents.middleware import AgentMiddleware
    from langchain.agents.middleware.types import ToolCallRequest

    _LC1 = True
except ImportError:
    AgentMiddleware = object  # type: ignore[assignment,misc]
    ToolCallRequest = object  # type: ignore[assignment,misc]
    _LC1 = False

from .tool_arg_repair import repair_tool_args

logger = logging.getLogger(__name__)


class ToolArgRepairMiddleware(AgentMiddleware if _LC1 else object):  # type: ignore[misc]
    """Auto-repair tool arguments as a pluggable middleware layer.

    Insert *before* ``DynamicToolMiddleware`` in the stack so that arguments
    are fixed before policy enforcement and Pydantic validation.
    """

    def __init__(self, *, tools: list[BaseTool] | None = None):
        self._tools: list[BaseTool] = tools or []

    def set_tools(self, tools: list[BaseTool]) -> None:
        self._tools = list(tools)

    def _repair(self, request: Any) -> Any:
        if not self._tools:
            return request
        tool_call = request.tool_call
        tool_name = str(tool_call.get("name", "") if isinstance(tool_call, dict) else getattr(tool_call, "name", ""))
        raw_args = tool_call.get("args", {}) if isinstance(tool_call, dict) else getattr(tool_call, "args", {})
        if not isinstance(raw_args, dict):
            return request

        repaired = repair_tool_args(tool_name, raw_args, self._tools)
        if repaired is not raw_args:
            if isinstance(tool_call, dict):
                tool_call["args"] = repaired
            elif hasattr(tool_call, "args"):
                tool_call.args = repaired  # type: ignore[attr-defined]
            logger.info("[ToolArgRepairMiddleware] Auto-repaired args for '%s'", tool_name)
        return request

    def wrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], ToolMessage | Command],
    ) -> ToolMessage | Command:
        request = self._repair(request)
        return handler(request)

    async def awrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], Awaitable[ToolMessage | Command]],
    ) -> ToolMessage | Command:
        request = self._repair(request)
        return await handler(request)
