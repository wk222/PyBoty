"""Shared execution metadata for capability invocations."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class CapabilityInvocation:
    """Structured record for one capability invocation attempt."""

    name: str
    success: bool
    duration_ms: float = 0.0
    source: str = ""
    layer: str = ""
    operation: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)
    invoked_at: float = 0.0

    def __post_init__(self) -> None:
        if not self.invoked_at:
            object.__setattr__(self, "invoked_at", time.time())

    @property
    def status(self) -> str:
        return "completed" if self.success else "failed"

    def to_context_payload(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "success": self.success,
            "status": self.status,
            "duration_ms": self.duration_ms,
            "source": self.source,
            "layer": self.layer,
            "operation": self.operation,
            "metadata": dict(self.metadata),
            "invoked_at": self.invoked_at,
        }

    def to_event_payload(self) -> dict[str, Any]:
        return {
            "duration_ms": self.duration_ms,
            "success": self.success,
            "layer": self.layer,
            "operation": self.operation,
            "metadata": dict(self.metadata),
        }
