"""LangChain middleware for capability-bus event recording.

Replaces the legacy MiddlewareStack-based BusMiddleware with proper
LangChain hooks:
- ``wrap_model_call`` — records model-call duration
- ``wrap_tool_call`` — records per-tool invocation events
"""

from __future__ import annotations

import time
from collections.abc import Awaitable, Callable
from typing import Any

from .capability_reporting import CapabilityBusReporter

try:
    from langchain.agents.middleware import AgentMiddleware
    from langchain.agents.middleware.types import ModelRequest, ModelResponse, ToolCallRequest
    from langchain_core.messages import ToolMessage
    from langgraph.types import Command

    _HAS_LC = True
except ImportError:
    _HAS_LC = False
    AgentMiddleware = object  # type: ignore[assignment,misc]
    ModelRequest = object  # type: ignore[assignment,misc]
    ModelResponse = object  # type: ignore[assignment,misc]
    ToolCallRequest = object  # type: ignore[assignment,misc]


class LCBusMiddleware(AgentMiddleware if _HAS_LC else object):  # type: ignore[misc]
    """Record capability-bus events for model calls and tool invocations."""

    def __init__(self, capability_bus: Any) -> None:
        self._bus = capability_bus
        self._reporter = CapabilityBusReporter(capability_bus)

    @property
    def name(self) -> str:
        return "LCBusMiddleware"

    def wrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], ModelResponse],
    ) -> ModelResponse:
        start = time.time()
        response = handler(request)
        duration = (time.time() - start) * 1000
        self._reporter.record_model_call(
            duration_ms=duration,
            message_count=len(getattr(request, "messages", []) or []),
            source="bus_middleware",
        )
        return response

    async def awrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], Awaitable[ModelResponse]],
    ) -> ModelResponse:
        start = time.time()
        response = await handler(request)
        duration = (time.time() - start) * 1000
        self._reporter.record_model_call(
            duration_ms=duration,
            message_count=len(getattr(request, "messages", []) or []),
            source="bus_middleware",
        )
        return response

    def wrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], ToolMessage | Command],
    ) -> ToolMessage | Command:
        start = time.time()
        result = handler(request)
        duration = (time.time() - start) * 1000
        tool_name = getattr(request, "name", "unknown")
        success = isinstance(result, ToolMessage)
        self._reporter.record_tool_call(
            tool_name=tool_name,
            success=success,
            duration_ms=duration,
            source="lc_bus_middleware",
            metadata={
                "request_type": type(request).__name__,
                "result_type": type(result).__name__,
            },
        )
        return result

    async def awrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], Awaitable[ToolMessage | Command]],
    ) -> ToolMessage | Command:
        start = time.time()
        result = await handler(request)
        duration = (time.time() - start) * 1000
        tool_name = getattr(request, "name", "unknown")
        success = isinstance(result, ToolMessage)
        self._reporter.record_tool_call(
            tool_name=tool_name,
            success=success,
            duration_ms=duration,
            source="lc_bus_middleware",
            metadata={
                "request_type": type(request).__name__,
                "result_type": type(result).__name__,
            },
        )
        return result
