"""Runtime state and persistence for the capability bus."""

from __future__ import annotations

import json
import threading
import time
import uuid
from collections import defaultdict
from collections.abc import Callable
from pathlib import Path
from typing import Any

from .capability_execution import CapabilityInvocation
from .capability_bus_models import BusEvent, Capability, CapabilityLayer, EventType


class CapabilityBusRuntime:
    """Own capability registration, events, shared context, and persistence."""

    def __init__(self, workspace_dir: str | Path = "workspace") -> None:
        self.workspace_dir = Path(workspace_dir).resolve()
        self._capabilities: dict[str, Capability] = {}
        self._event_handlers: dict[EventType, list[Callable[[BusEvent], None]]] = defaultdict(list)
        self._shared_context: dict[str, Any] = {}
        self._event_log: list[BusEvent] = []
        self._lock = threading.Lock()
        self._max_log = 500
        self._registry_path = self.workspace_dir / "data" / "capability_registry.json"
        self._load_registry()

    def register(
        self,
        name: str,
        layer: CapabilityLayer,
        *,
        description: str = "",
        tags: list[str] | None = None,
        dependencies: list[str] | None = None,
        provides: list[str] | None = None,
        metadata: dict[str, Any] | None = None,
        registered_by: str = "",
        origin_path: str = "",
    ) -> Capability:
        with self._lock:
            existing = self._capabilities.get(name)
            prior_stats = (
                (existing.invoke_count, existing.success_count, existing.total_duration_ms, existing.last_invoked)
                if existing
                else (0, 0, 0.0, 0.0)
            )
        capability = Capability(
            name=name,
            layer=layer,
            description=description,
            tags=list(tags or []),
            dependencies=list(dependencies or []),
            provides=list(provides or []),
            metadata=dict(metadata or {}),
            registered_by=registered_by,
            origin_path=origin_path,
            invoke_count=prior_stats[0],
            success_count=prior_stats[1],
            total_duration_ms=prior_stats[2],
            last_invoked=prior_stats[3],
        )
        with self._lock:
            self._capabilities[name] = capability
        self.emit(EventType.CAPABILITY_REGISTERED, name, {"layer": layer.value})
        return capability

    def unregister(self, name: str) -> None:
        with self._lock:
            self._capabilities.pop(name, None)

    def get(self, name: str) -> Capability | None:
        return self._capabilities.get(name)

    def list_capabilities(self) -> list[Capability]:
        return list(self._capabilities.values())

    def find(
        self,
        *,
        layer: CapabilityLayer | None = None,
        tag: str | None = None,
        provides: str | None = None,
    ) -> list[Capability]:
        results = []
        for capability in self._capabilities.values():
            if layer and capability.layer != layer:
                continue
            if tag and tag not in capability.tags:
                continue
            if provides and provides not in capability.provides:
                continue
            results.append(capability)
        return results

    def find_by_dependency(self, capability_name: str) -> list[Capability]:
        return [capability for capability in self._capabilities.values() if capability_name in capability.dependencies]

    def share_execution_context(self, invocation: CapabilityInvocation) -> None:
        """Publish the latest structured capability invocation to shared context."""
        self.share_context(
            "last_capability_execution",
            invocation.to_context_payload(),
            source=invocation.source or invocation.name,
        )

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
        invocation = CapabilityInvocation(
            name=name,
            success=success,
            duration_ms=duration_ms,
            source=source,
            layer=layer,
            operation=operation,
            metadata=dict(metadata or {}),
        )
        with self._lock:
            capability = self._capabilities.get(name)
            if capability:
                capability.invoke_count += 1
                if success:
                    capability.success_count += 1
                capability.total_duration_ms += duration_ms
                capability.last_invoked = time.time()
                if not invocation.layer:
                    invocation = CapabilityInvocation(
                        name=invocation.name,
                        success=invocation.success,
                        duration_ms=invocation.duration_ms,
                        source=invocation.source,
                        layer=capability.layer.value,
                        operation=invocation.operation,
                        metadata=invocation.metadata,
                        invoked_at=invocation.invoked_at,
                    )
        self.share_execution_context(invocation)
        self.emit(EventType.CAPABILITY_INVOKED, invocation.source or name, invocation.to_event_payload())
        event_type = EventType.CAPABILITY_COMPLETED if success else EventType.CAPABILITY_FAILED
        self.emit(event_type, name, invocation.to_event_payload())

    def on(self, event_type: EventType, handler: Callable[[BusEvent], None]) -> None:
        self._event_handlers[event_type].append(handler)

    def off(self, event_type: EventType, handler: Callable[[BusEvent], None]) -> None:
        handlers = self._event_handlers.get(event_type, [])
        if handler in handlers:
            handlers.remove(handler)

    def emit(
        self,
        event_type: EventType,
        source: str,
        data: dict[str, Any] | None = None,
    ) -> None:
        event = BusEvent(type=event_type, source=source, data=data or {})
        with self._lock:
            self._event_log.append(event)
            if len(self._event_log) > self._max_log:
                self._event_log = self._event_log[-self._max_log :]

        for handler in list(self._event_handlers.get(event_type, [])):
            try:
                handler(event)
            except Exception as exc:
                print(f"[CapBus] 事件处理失败: {event_type.value} → {exc}")

    def share_context(self, key: str, value: Any, *, source: str = "") -> None:
        with self._lock:
            self._shared_context[key] = {
                "value": value,
                "source": source,
                "updated_at": time.time(),
            }
        self.emit(EventType.CONTEXT_UPDATED, source, {"key": key})

    def get_context(self, key: str) -> Any:
        entry = self._shared_context.get(key)
        return entry["value"] if entry else None

    def get_all_context(self) -> dict[str, Any]:
        return {key: value["value"] for key, value in self._shared_context.items()}

    def share_data(self, key: str, data: Any, *, source: str = "", ttl_seconds: int = 0) -> None:
        with self._lock:
            self._shared_context[f"data:{key}"] = {
                "data": data,
                "source": source,
                "shared_at": time.time(),
                "ttl": ttl_seconds,
            }
        self.emit(EventType.DATA_SHARED, source, {"key": key, "size": len(str(data))})

    def get_data(self, key: str) -> Any:
        entry = self._shared_context.get(f"data:{key}")
        if not entry:
            return None
        ttl = int(entry.get("ttl", 0) or 0)
        if ttl and time.time() - float(entry["shared_at"]) > ttl:
            self._shared_context.pop(f"data:{key}", None)
            return None
        return entry.get("data")

    def resolve_dependencies(self, capability_name: str) -> dict[str, Any]:
        capability = self._capabilities.get(capability_name)
        if not capability:
            return {"resolved": False, "error": f"能力 '{capability_name}' 不存在"}

        missing: list[str] = []
        resolved: list[str] = []
        for dependency in capability.dependencies:
            if dependency in self._capabilities:
                resolved.append(dependency)
            else:
                missing.append(dependency)
        return {
            "resolved": not missing,
            "capability": capability_name,
            "dependencies": capability.dependencies,
            "resolved_deps": resolved,
            "missing_deps": missing,
        }

    def get_layer_graph(self) -> dict[str, Any]:
        layers: dict[str, Any] = {}
        for layer in CapabilityLayer:
            capabilities = self.find(layer=layer)
            layers[layer.value] = {
                "count": len(capabilities),
                "capabilities": [capability.name for capability in capabilities],
                "total_invocations": sum(capability.invoke_count for capability in capabilities),
            }

        connections = []
        for capability in self._capabilities.values():
            for dependency in capability.dependencies:
                if dependency in self._capabilities:
                    connections.append(
                        {
                            "from": capability.name,
                            "from_layer": capability.layer.value,
                            "to": dependency,
                            "to_layer": self._capabilities[dependency].layer.value,
                        }
                    )
        return {
            "layers": layers,
            "connections": connections,
            "total_capabilities": len(self._capabilities),
        }

    def get_stats(self) -> dict[str, Any]:
        by_layer: defaultdict[str, dict[str, int]] = defaultdict(lambda: {"count": 0, "invocations": 0, "successes": 0})
        for capability in self._capabilities.values():
            layer = by_layer[capability.layer.value]
            layer["count"] += 1
            layer["invocations"] += capability.invoke_count
            layer["successes"] += capability.success_count

        top_used = sorted(self._capabilities.values(), key=lambda item: item.invoke_count, reverse=True)[:10]
        return {
            "total_capabilities": len(self._capabilities),
            "by_layer": dict(by_layer),
            "top_used": [
                {"name": capability.name, "layer": capability.layer.value, "invocations": capability.invoke_count}
                for capability in top_used
            ],
            "event_log_size": len(self._event_log),
            "shared_context_keys": list(self._shared_context.keys())[:20],
        }

    def get_recent_events(self, n: int = 20) -> list[dict[str, Any]]:
        return [
            {
                "type": event.type.value,
                "source": event.source,
                "data": event.data,
                "time": event.timestamp,
            }
            for event in self._event_log[-n:]
        ]

    def save_registry(self) -> None:
        with self._lock:
            payload = {
                "version": "1.1",
                "saved_at": time.time(),
                "capabilities": {name: cap.to_dict() for name, cap in self._capabilities.items()},
            }
        self._registry_path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = self._registry_path.with_name(f"{self._registry_path.name}.{uuid.uuid4().hex}.tmp")
        try:
            tmp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
            tmp_path.replace(self._registry_path)
        except Exception:
            if tmp_path.exists():
                tmp_path.unlink(missing_ok=True)

    def _load_registry(self) -> None:
        """Load persisted registry snapshot on startup to restore stats/provenance."""
        if not self._registry_path.exists():
            return
        try:
            raw = json.loads(self._registry_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return
        capabilities = raw.get("capabilities", {})
        if not isinstance(capabilities, dict):
            return
        for name, cap_data in capabilities.items():
            if not isinstance(cap_data, dict):
                continue
            cap_data.setdefault("name", name)
            self._capabilities[name] = Capability.from_dict(cap_data)

    def auto_register_tools(self, tools: list[Any]) -> None:
        for tool in tools:
            name = getattr(tool, "name", str(tool))
            description = str(getattr(tool, "description", ""))[:200]
            if name not in self._capabilities:
                self.register(name, CapabilityLayer.TOOL, description=description)

    def auto_register_skills(self, skill_registry: Any) -> None:
        for skill_name, skill_def in getattr(skill_registry, "skills", {}).items():
            description = getattr(skill_def, "description", "")
            raw_tools = getattr(skill_def, "tools", [])
            tool_names = []
            for tool in raw_tools:
                if hasattr(tool, "name"):
                    tool_names.append(tool.name)
                elif isinstance(tool, dict):
                    tool_names.append(tool.get("name", str(tool)))
                else:
                    tool_names.append(str(tool))
            self.register(
                skill_name,
                CapabilityLayer.SKILL,
                description=description,
                provides=tool_names,
                tags=getattr(skill_def, "tags", []),
            )

    def auto_register_agents(self, agent_storage: Any) -> None:
        agents_dict = getattr(agent_storage, "agents", {})
        for agent_name, agent_def in agents_dict.items():
            description = ""
            if hasattr(agent_def, "role"):
                description = agent_def.role
            elif isinstance(agent_def, dict):
                description = agent_def.get("role", "")
            self.register(
                agent_name,
                CapabilityLayer.AGENT,
                description=description,
                tags=["sub-agent"],
            )

    def auto_register_apps(self, app_manager: Any) -> None:
        for app_name in getattr(app_manager, "apps", {}):
            app_def = app_manager.apps[app_name]
            self.register(
                app_name,
                CapabilityLayer.APP,
                description=getattr(app_def, "description", ""),
                provides=list(getattr(app_def, "exports", [])),
                tags=getattr(app_def, "tags", []),
                metadata={
                    "api_enabled": bool(getattr(app_def, "api_enabled", False)),
                    "require_auth": bool(getattr(app_def, "require_auth", False)),
                    "mode": getattr(app_def, "mode", "static"),
                    "exports": list(getattr(app_def, "exports", [])),
                    "shared_datastores": list(getattr(app_def, "shared_datastores", [])),
                    "data_contracts": [dict(item) for item in getattr(app_def, "data_contracts", [])],
                },
            )

    def auto_register_workflows(self, pyflow_engine: Any) -> None:
        for workflow in pyflow_engine.list_workflow_files():
            self.register(
                workflow["name"],
                CapabilityLayer.WORKFLOW,
                description=workflow.get("description", ""),
                tags=workflow.get("tags", []),
            )
