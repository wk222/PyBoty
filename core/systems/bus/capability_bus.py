"""Thin capability-bus facade over runtime, reporting, and tool surfaces."""

from __future__ import annotations

from typing import Any

from .capability_bus_models import CapabilityLayer, EventType
from .capability_bus_runtime import CapabilityBusRuntime
from .capability_bus_tools import CapBusQueryInput, CapBusTool, get_capability_bus_tools


class CapabilityBus:
    """Thin façade over the capability-bus runtime."""

    def __init__(self, workspace_dir: str = "workspace"):
        self.runtime = CapabilityBusRuntime(workspace_dir)

    def register(
        self,
        name: str,
        layer: CapabilityLayer,
        description: str = "",
        tags: list[str] | None = None,
        dependencies: list[str] | None = None,
        provides: list[str] | None = None,
        metadata: dict[str, Any] | None = None,
        registered_by: str = "",
        origin_path: str = "",
    ) -> Any:
        return self.runtime.register(
            name,
            layer,
            description=description,
            tags=tags,
            dependencies=dependencies,
            provides=provides,
            metadata=metadata,
            registered_by=registered_by,
            origin_path=origin_path,
        )

    def unregister(self, name: str) -> None:
        self.runtime.unregister(name)

    def get(self, name: str) -> Any:
        return self.runtime.get(name)

    def list_capabilities(self) -> list[Any]:
        return self.runtime.list_capabilities()

    def find(
        self,
        layer: CapabilityLayer | None = None,
        tag: str | None = None,
        provides: str | None = None,
    ) -> list[Any]:
        return self.runtime.find(layer=layer, tag=tag, provides=provides)

    def find_by_dependency(self, capability_name: str) -> list[Any]:
        return self.runtime.find_by_dependency(capability_name)

    def share_execution_context(self, invocation: Any) -> None:
        self.runtime.share_execution_context(invocation)

    def record_invocation(
        self,
        name: str,
        success: bool,
        duration_ms: float = 0,
        *,
        source: str = "",
        layer: str = "",
        operation: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> None:
        self.runtime.record_invocation(
            name,
            success,
            duration_ms,
            source=source,
            layer=layer,
            operation=operation,
            metadata=metadata,
        )

    def on(self, event_type: EventType, handler: Any) -> None:
        self.runtime.on(event_type, handler)

    def off(self, event_type: EventType, handler: Any) -> None:
        self.runtime.off(event_type, handler)

    def share_context(self, key: str, value: Any, source: str = "") -> None:
        self.runtime.share_context(key, value, source=source)

    def get_context(self, key: str) -> Any:
        return self.runtime.get_context(key)

    def get_all_context(self) -> dict[str, Any]:
        return self.runtime.get_all_context()

    def share_data(self, key: str, data: Any, source: str = "", ttl_seconds: int = 0) -> None:
        self.runtime.share_data(key, data, source=source, ttl_seconds=ttl_seconds)

    def get_data(self, key: str) -> Any:
        return self.runtime.get_data(key)

    def resolve_dependencies(self, capability_name: str) -> dict[str, Any]:
        return self.runtime.resolve_dependencies(capability_name)

    def get_layer_graph(self) -> dict[str, Any]:
        return self.runtime.get_layer_graph()

    def get_stats(self) -> dict[str, Any]:
        return self.runtime.get_stats()

    def get_recent_events(self, n: int = 20) -> list[dict[str, Any]]:
        return self.runtime.get_recent_events(n)

    def save_registry(self) -> None:
        self.runtime.save_registry()

    def auto_register_tools(self, tools: list[Any]) -> None:
        self.runtime.auto_register_tools(tools)

    def auto_register_skills(self, skill_registry: Any) -> None:
        self.runtime.auto_register_skills(skill_registry)

    def auto_register_agents(self, agent_storage: Any) -> None:
        self.runtime.auto_register_agents(agent_storage)

    def auto_register_apps(self, app_manager: Any) -> None:
        self.runtime.auto_register_apps(app_manager)

    def auto_register_workflows(self, pyflow_engine: Any) -> None:
        self.runtime.auto_register_workflows(pyflow_engine)
