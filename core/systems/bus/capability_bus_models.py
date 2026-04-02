"""Shared models for the capability bus."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class CapabilityLayer(str, Enum):
    TOOL = "tool"
    SKILL = "skill"
    AGENT = "agent"
    WORKFLOW = "workflow"
    APP = "app"


class EventType(str, Enum):
    CAPABILITY_REGISTERED = "capability_registered"
    CAPABILITY_INVOKED = "capability_invoked"
    CAPABILITY_COMPLETED = "capability_completed"
    CAPABILITY_FAILED = "capability_failed"
    DATA_SHARED = "data_shared"
    WORKFLOW_STARTED = "workflow_started"
    WORKFLOW_COMPLETED = "workflow_completed"
    APP_REQUESTED = "app_requested"
    CONTEXT_UPDATED = "context_updated"


@dataclass
class Capability:
    name: str
    layer: CapabilityLayer
    description: str = ""
    tags: list[str] = field(default_factory=list)
    dependencies: list[str] = field(default_factory=list)
    provides: list[str] = field(default_factory=list)
    invoke_count: int = 0
    success_count: int = 0
    total_duration_ms: float = 0
    last_invoked: float = 0
    metadata: dict[str, Any] = field(default_factory=dict)
    registered_by: str = ""
    origin_path: str = ""
    registered_at: float = 0.0

    def __post_init__(self) -> None:
        if not self.registered_at:
            self.registered_at = time.time()

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "layer": self.layer.value,
            "description": self.description,
            "tags": self.tags,
            "dependencies": self.dependencies,
            "provides": self.provides,
            "metadata": self.metadata,
            "invoke_count": self.invoke_count,
            "success_count": self.success_count,
            "total_duration_ms": self.total_duration_ms,
            "avg_duration_ms": round(self.total_duration_ms / self.invoke_count, 1) if self.invoke_count else 0,
            "success_rate": f"{self.success_count / self.invoke_count * 100:.0f}%" if self.invoke_count else "N/A",
            "last_invoked": self.last_invoked,
            "registered_by": self.registered_by,
            "origin_path": self.origin_path,
            "registered_at": self.registered_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Capability:
        layer_raw = data.get("layer", "tool")
        try:
            layer = CapabilityLayer(layer_raw)
        except ValueError:
            layer = CapabilityLayer.TOOL
        return cls(
            name=str(data.get("name", "")),
            layer=layer,
            description=str(data.get("description", "")),
            tags=list(data.get("tags", [])),
            dependencies=list(data.get("dependencies", [])),
            provides=list(data.get("provides", [])),
            invoke_count=int(data.get("invoke_count", 0)),
            success_count=int(data.get("success_count", 0)),
            total_duration_ms=float(data.get("total_duration_ms", 0)),
            last_invoked=float(data.get("last_invoked", 0)),
            metadata=dict(data.get("metadata", {})),
            registered_by=str(data.get("registered_by", "")),
            origin_path=str(data.get("origin_path", "")),
            registered_at=float(data.get("registered_at", 0)),
        )


@dataclass
class BusEvent:
    type: EventType
    source: str
    data: dict[str, Any] = field(default_factory=dict)
    timestamp: float = 0

    def __post_init__(self) -> None:
        if not self.timestamp:
            self.timestamp = time.time()
