"""Shared reporting helpers for capability-bus execution telemetry."""

from __future__ import annotations

from typing import Any


class CapabilityBusReporter:
    """Emit consistent bus telemetry across legacy and modern middleware."""

    def __init__(self, capability_bus: Any) -> None:
        self._bus = capability_bus

    def record_model_call(
        self,
        *,
        duration_ms: float,
        message_count: int = 0,
        source: str = "bus_middleware",
    ) -> None:
        self._bus.share_context("last_invoke_duration_ms", duration_ms, source=source)
        self._bus.share_context(
            "last_model_call",
            {
                "duration_ms": duration_ms,
                "message_count": message_count,
            },
            source=source,
        )

    def record_tool_call(
        self,
        *,
        tool_name: str,
        success: bool,
        duration_ms: float,
        source: str,
        operation: str = "tool_call",
        metadata: dict[str, Any] | None = None,
    ) -> None:
        self._bus.record_invocation(
            tool_name,
            success=success,
            duration_ms=duration_ms,
            source=source,
            layer="tool",
            operation=operation,
            metadata=dict(metadata or {}),
        )

    def record_capability_invocation(
        self,
        *,
        name: str,
        success: bool,
        duration_ms: float,
        source: str,
        layer: str = "",
        operation: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> None:
        self._bus.record_invocation(
            name,
            success=success,
            duration_ms=duration_ms,
            source=source,
            layer=layer,
            operation=operation,
            metadata=dict(metadata or {}),
        )
