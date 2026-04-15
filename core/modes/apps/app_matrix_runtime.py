"""Service layer for the APP Brain mode.

This runtime keeps the orchestration registry aligned with real apps and
provides higher-level helpers for app-to-app coordination plans.
"""

from __future__ import annotations

from typing import Any

from .app_manager import AppDefinition, AppManager
from .app_orchestration import AppOrchestrationRegistry, NodeStatus, NodeType, OrchestrationNode


class AppMatrixRuntime:
    """Coordinate app topology for the APP Brain root mode."""

    def __init__(
        self,
        *,
        app_manager: AppManager,
        orchestration_registry: AppOrchestrationRegistry,
        capability_registry: Any | None = None,
    ) -> None:
        self.app_manager = app_manager
        self.registry = orchestration_registry
        self.capability_registry = capability_registry

    def sync_apps(self, *, clear_missing: bool = False) -> dict[str, Any]:
        """Sync generated apps into the orchestration registry."""
        synced_nodes: list[dict[str, Any]] = []
        current_app_names = {app.name for app in self.app_manager.apps.values()}

        for app in self.app_manager.apps.values():
            node = self.registry.upsert_node(
                app.name,
                NodeType.APP,
                description=app.description,
                domain=self._resolve_domain(app),
                owner=app.author,
                metadata=self._build_app_metadata(app),
            )
            self.registry.update_node_status(
                node.node_id,
                NodeStatus.ACTIVE if app.enabled else NodeStatus.INACTIVE,
            )
            synced_nodes.append(node.to_dict())

        removed_nodes: list[str] = []
        if clear_missing:
            for node in self.registry.list_nodes(node_type=NodeType.APP):
                if node.metadata.get("source") != "app_manager":
                    continue
                if node.name in current_app_names:
                    continue
                if self.registry.unregister_node(node.node_id):
                    removed_nodes.append(node.name)

        return {
            "synced": synced_nodes,
            "removed": removed_nodes,
            "issues": self.registry.validate_graph(),
            "topology_stats": self.registry.get_topology()["stats"],
        }

    def connect_apps(
        self,
        source_app: str,
        target_app: str,
        *,
        source_port: str = "default",
        target_port: str = "default",
        description: str = "",
        transform: str = "",
    ) -> dict[str, Any]:
        source_node = self._require_app_node(source_app)
        target_node = self._require_app_node(target_app)
        binding = self.registry.add_binding(
            source_node.node_id,
            source_port,
            target_node.node_id,
            target_port,
            description=description,
            transform=transform,
        )
        return binding.to_dict()

    def register_pipeline(
        self,
        name: str,
        app_names: list[str],
        *,
        description: str = "",
        schedule: str = "",
    ) -> dict[str, Any]:
        node_ids = [self._require_app_node(app_name).node_id for app_name in app_names]
        pipeline = self.registry.register_pipeline(
            name,
            node_ids,
            description=description,
            schedule=schedule,
        )
        return pipeline.to_dict()

    def get_app_summary(self, app_name: str) -> dict[str, Any] | None:
        node = self.registry.find_node(app_name, node_type=NodeType.APP)
        if node is None:
            return None
        summary = self.registry.get_node_summary(node.node_id)
        if summary is None:
            return None
        summary["contracts"] = self._extract_contract_metadata(node)
        return summary

    def get_overview(self) -> dict[str, Any]:
        apps = [app.to_dict() for app in self.app_manager.apps.values()]
        topology = self.registry.get_topology()
        service_marketplace = self.discover_services()
        return {
            "apps": apps,
            "topology": topology,
            "issues": self.registry.validate_graph(),
            "services": service_marketplace,
        }

    def discover_services(self, *, query: str = "", provides: str = "") -> dict[str, Any]:
        if self.capability_registry is None:
            return {"query": query, "provides": provides, "providers": [], "count": 0}
        lookup = provides or query
        if lookup:
            providers = self.capability_registry.find_providers(lookup, layer="app")
            providers["query"] = query
            return providers
        local = self.capability_registry.discover(layer="app", query=query, include_marketplace=False)
        return {
            "query": query,
            "provides": provides,
            "providers": local["local"],
            "count": len(local["local"]),
        }

    def request_service_grant(
        self,
        *,
        caller_app: str,
        target_app: str = "",
        capability_name: str = "",
        provides: str = "",
        requested_quota: int = 1,
        ttl_seconds: int = 3600,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if self.capability_registry is None:
            return {"success": False, "error": "Capability registry unavailable"}
        self.sync_apps()
        return self.capability_registry.issue_app_grant(
            caller_app=caller_app,
            target_app=target_app,
            capability_name=capability_name,
            provides=provides,
            requested_quota=requested_quota,
            ttl_seconds=ttl_seconds,
            metadata=metadata,
        )

    def list_service_grants(
        self,
        *,
        caller_app: str = "",
        provider_app: str = "",
    ) -> list[dict[str, Any]]:
        if self.capability_registry is None:
            return []
        return self.capability_registry.list_grants(
            caller_app=caller_app,
            provider_app=provider_app,
        )

    def invoke_service(
        self,
        *,
        caller_app: str,
        grant_token: str,
        action: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        if self.capability_registry is None:
            return {"success": False, "error": "Capability registry unavailable"}
        return self.capability_registry.invoke_app_capability(
            caller_app=caller_app,
            grant_token=grant_token,
            action=action,
            payload=payload,
        )

    def update_app_contract_metadata(
        self,
        app_name: str,
        *,
        shared_datastores: list[str] | None = None,
        shared_schemas: list[dict[str, Any]] | None = None,
        data_contracts: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        """Update durable contract metadata for one app and resync its node."""
        result = self.app_manager.update_app_topology_metadata(
            app_name,
            shared_datastores=shared_datastores,
            shared_schemas=shared_schemas,
            data_contracts=data_contracts,
        )
        if not result.get("success"):
            raise KeyError(result.get("error", f"App '{app_name}' not found"))
        self.sync_apps()
        return {
            "app": result["app"],
            "summary": self.get_app_summary(app_name),
        }

    @staticmethod
    def _resolve_domain(app: AppDefinition) -> str:
        if app.tags:
            return str(app.tags[0])
        if app.mode:
            return str(app.mode)
        return "app"

    @staticmethod
    def _build_app_metadata(app: AppDefinition) -> dict[str, Any]:
        return {
            "source": "app_manager",
            "app_name": app.name,
            "display_name": app.display_name,
            "mode": app.mode,
            "enabled": app.enabled,
            "entry_point": app.entry_point,
            "api_enabled": app.api_enabled,
            "tags": list(app.tags),
            "agent_binding": app.agent_binding,
            "workflow_binding": app.workflow_binding,
            "knowledge_collections": list(app.knowledge_collections),
            "allowed_tools": list(app.allowed_tools),
            "shared_datastores": list(app.shared_datastores),
            "shared_schemas": [dict(item) for item in app.shared_schemas],
            "data_contracts": [dict(item) for item in app.data_contracts],
        }

    @staticmethod
    def _extract_contract_metadata(node: OrchestrationNode) -> dict[str, Any]:
        metadata = node.metadata
        return {
            "shared_datastores": list(metadata.get("shared_datastores", [])),
            "shared_schemas": [dict(item) for item in metadata.get("shared_schemas", [])],
            "data_contracts": [dict(item) for item in metadata.get("data_contracts", [])],
        }

    def _require_app_node(self, app_name: str) -> OrchestrationNode:
        node = self.registry.find_node(app_name, node_type=NodeType.APP)
        if node is None:
            raise KeyError(f"App '{app_name}' is not registered in the APP Brain topology")
        return node
