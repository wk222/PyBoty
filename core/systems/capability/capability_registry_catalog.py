"""Catalog/discovery runtime behind the capability registry facade."""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    from core.systems.runtime.backend_protocol import (
        SkillMarketplaceProtocol,
        UnifiedAssetInventoryProtocol,
    )

from core.systems.integration.pyhub_client import PyHubClient
from core.systems.runtime.event_bus import Event, EventType, event_bus

from .capability_bus import CapabilityBus
from .capability_bus_models import Capability, CapabilityLayer
from .capability_tree import (
    annotate_capability_tree_metadata,
    build_capability_route_projection,
    selection_metadata_for_capability,
)


class CapabilityCatalogRuntime:
    """Own local indexing, discovery, contracts, and marketplace exchange."""

    def __init__(
        self,
        *,
        workspace_dir: str | Path,
        capability_bus: CapabilityBus,
        skill_marketplace: SkillMarketplaceProtocol,
        skill_registry: Any | None = None,
        app_manager: Any | None = None,
        agent_storage: Any | None = None,
        pyflow_engine: Any | None = None,
        hub_client_factory: Callable[[str, str | None], PyHubClient] | None = None,
    ) -> None:
        self.workspace_dir = Path(workspace_dir)
        self.capability_bus = capability_bus
        self.skill_marketplace = skill_marketplace
        self.skill_registry = skill_registry
        self.app_manager = app_manager
        self.agent_storage = agent_storage
        self.pyflow_engine = pyflow_engine
        self._hub_client_factory = hub_client_factory or (lambda url, token=None: PyHubClient(url, api_key=token))

    def refresh_local_index(
        self,
        *,
        tools: list[Any] | None = None,
        unified_inventory: UnifiedAssetInventoryProtocol | None = None,
        save: bool = True,
    ) -> dict[str, Any]:
        if unified_inventory is not None:
            self._register_from_unified_inventory(unified_inventory)
        else:
            if tools:
                self.capability_bus.auto_register_tools(tools)
            if self.skill_registry is not None:
                self.capability_bus.auto_register_skills(self.skill_registry)
        if self.pyflow_engine is not None:
            self.capability_bus.auto_register_workflows(self.pyflow_engine)
        if self.app_manager is not None:
            self.capability_bus.auto_register_apps(self.app_manager)
        if self.agent_storage is not None:
            self.capability_bus.auto_register_agents(self.agent_storage)
        if save:
            self.capability_bus.save_registry()
        return self.get_registry_snapshot()

    def discover(
        self,
        *,
        query: str = "",
        layer: str = "",
        tag: str = "",
        provides: str = "",
        include_marketplace: bool = True,
        include_hub: bool = False,
        hub_url: str = "",
        hub_token: str = "",
        hub_type: str = "skill",
        page: int = 1,
    ) -> dict[str, Any]:
        self.refresh_local_index(save=False)
        projected_runtime_view = self.capability_bus.get_context("projected_runtime_view")
        local = [
            self._serialize_capability_for_selection(
                capability, query=query, provides=provides, projected_runtime_view=projected_runtime_view
            )
            for capability in self._filter_local_capabilities(
                query=query,
                layer=layer,
                tag=tag,
                provides=provides,
                projected_runtime_view=projected_runtime_view,
            )
        ]
        marketplace = self._discover_marketplace(query=query, provides=provides) if include_marketplace else []
        hub = (
            self._discover_hub(
                query=query or provides,
                hub_url=hub_url,
                hub_token=hub_token,
                hub_type=hub_type,
                page=page,
            )
            if include_hub and hub_url.strip()
            else []
        )
        payload = {
            "query": query,
            "filters": {
                "layer": layer,
                "tag": tag,
                "provides": provides,
                "include_marketplace": include_marketplace,
                "include_hub": include_hub,
            },
            "local": local,
            "marketplace": marketplace,
            "hub": hub,
            "counts": {
                "local": len(local),
                "marketplace": len(marketplace),
                "hub": len(hub),
            },
        }
        event_bus.emit(
            Event(
                type=EventType.CAPABILITY_DISCOVERED,
                source="capability_registry",
                payload={"query": query, "provides": provides, "counts": payload["counts"]},
            )
        )
        return payload

    def find_providers(self, provides: str, *, layer: str = "") -> dict[str, Any]:
        self.refresh_local_index(save=False)
        capabilities = self._filter_local_capabilities(provides=provides, layer=layer)
        return {
            "provides": provides,
            "providers": [
                self._serialize_capability_for_selection(capability, query=provides, provides=provides)
                for capability in capabilities
            ],
            "count": len(capabilities),
        }

    def get_capability_contract(self, capability_name: str) -> dict[str, Any] | None:
        self.refresh_local_index(save=False)
        capability = self.capability_bus.get(capability_name)
        if capability is None:
            return None
        tree_metadata = annotate_capability_tree_metadata(
            name=capability.name,
            layer=capability.layer,
            description=capability.description,
            tags=capability.tags,
            dependencies=capability.dependencies,
            provides=capability.provides,
            metadata=capability.metadata,
        ).get("tree", {})
        contract = {
            "id": capability.name,
            "layer": capability.layer.value,
            "description": capability.description,
            "tags": list(capability.tags),
            "provides": list(capability.provides),
            "dependencies": list(capability.dependencies),
            "metadata": dict(capability.metadata),
            "tree": dict(tree_metadata),
            "interface": self._build_interface_contract(capability),
        }
        if capability.layer == CapabilityLayer.APP and self.app_manager is not None:
            app_def = self.app_manager.get_app(capability.name)
            if app_def is not None:
                contract["app_contract"] = {
                    "mode": getattr(app_def, "mode", ""),
                    "entry_point": getattr(app_def, "entry_point", ""),
                    "api_enabled": getattr(app_def, "api_enabled", False),
                    "require_auth": getattr(app_def, "require_auth", False),
                    "exports": list(getattr(app_def, "exports", [])),
                    "shared_datastores": list(getattr(app_def, "shared_datastores", [])),
                    "shared_schemas": [dict(item) for item in getattr(app_def, "shared_schemas", [])],
                    "data_contracts": [dict(item) for item in getattr(app_def, "data_contracts", [])],
                }
        if capability.layer == CapabilityLayer.SKILL and self.skill_registry is not None:
            skill_def = self.skill_registry.get_skill(capability.name)
            if skill_def is not None:
                contract["skill_contract"] = {
                    "source": getattr(skill_def, "source_name", ""),
                    "enabled": getattr(skill_def, "enabled", True),
                    "capabilities": list(getattr(skill_def, "capabilities", [])),
                    "tool_names": [tool.get("name", "") for tool in getattr(skill_def, "tools", [])],
                    "requires_bins": list(getattr(skill_def, "requires_bins", [])),
                    "requires_config": list(getattr(skill_def, "requires_config", [])),
                    "primary_env": getattr(skill_def, "primary_env", ""),
                    "openclaw_metadata": dict(getattr(skill_def, "openclaw_metadata", {})),
                }
        return contract

    def publish_skill(
        self,
        skill_name: str,
        *,
        version: str = "0.1.0",
        changelog: str = "",
        publish_to_hub: bool = False,
        hub_url: str = "",
        hub_token: str = "",
    ) -> dict[str, Any]:
        package_result = self.skill_marketplace.package_skill(skill_name)
        if not package_result.get("success"):
            return package_result
        remote_result: dict[str, Any] | None = None
        if publish_to_hub:
            skill_dir = self._skill_dir(skill_name)
            if skill_dir is None:
                return {"success": False, "error": f"Skill '{skill_name}' not found for remote publish"}
            client = self._hub_client_factory(hub_url, hub_token or None)
            remote_result = client.publish(
                str(skill_dir),
                pkg_type="skill",
                version=version,
                changelog=changelog,
            )
        self.refresh_local_index(save=True)
        payload = {
            "success": True,
            "skill_name": skill_name,
            "package": package_result,
            "remote": remote_result,
        }
        event_bus.emit(
            Event(
                type=EventType.CAPABILITY_PUBLISHED,
                source="capability_registry",
                payload={"kind": "skill", "name": skill_name, "remote": bool(remote_result), "version": version},
            )
        )
        return payload

    def install_skill(
        self,
        *,
        package_path: str = "",
        url: str = "",
        github_repo: str = "",
        github_subpath: str = "",
        hub_slug: str = "",
        version: str = "latest",
        hub_url: str = "",
        hub_token: str = "",
    ) -> dict[str, Any]:
        if hub_slug:
            if not hub_url.strip():
                return {"success": False, "error": "hub_url is required for hub installs"}
            client = self._hub_client_factory(hub_url, hub_token or None)
            installed_dir = client.install(hub_slug, version=version, target_dir=self.skill_marketplace.skills_dir)
            result = {"success": True, "skill_name": hub_slug, "path": str(installed_dir), "source": "hub"}
        elif github_repo:
            result = self.skill_marketplace.install_from_github(repo=github_repo, subpath=github_subpath)
        elif url:
            result = self.skill_marketplace.install_from_url(url=url)
        elif package_path:
            result = self.skill_marketplace.install_skill(package_path)
        else:
            result = {"success": False, "error": "Provide package_path, url, github_repo, or hub_slug"}
        if result.get("success"):
            if self.skill_registry is not None:
                self.skill_registry.reload()
            self.refresh_local_index(save=True)
            event_bus.emit(
                Event(
                    type=EventType.CAPABILITY_INSTALLED,
                    source="capability_registry",
                    payload={
                        "kind": "skill",
                        "name": result.get("skill_name", hub_slug),
                        "source": result.get("source", "marketplace"),
                    },
                )
            )
        return result

    def get_registry_snapshot(self) -> dict[str, Any]:
        capabilities = [
            self._serialize_capability_for_selection(capability)
            for capability in self._filter_local_capabilities()
        ]
        summary: dict[str, Any] = {
            "stats": self.capability_bus.get_stats(),
            "graph": self.capability_bus.get_layer_graph(),
            "tree": self.capability_bus.get_tree_projection(),
            "capabilities": capabilities,
            "marketplace": {
                "catalog": sorted(self.skill_marketplace.list_available(), key=lambda item: item.get("name", "")),
                "discovered": sorted(
                    self.skill_marketplace.discover_skills(),
                    key=lambda item: item.get("name", ""),
                ),
            },
        }
        if self.skill_registry is not None:
            summary["sources"] = self.skill_registry.list_sources()
        if self.app_manager is not None:
            apps = self.app_manager.list_apps()
            summary["apps"] = {"count": len(apps), "items": apps}
        return summary

    def route_query(
        self,
        *,
        query: str = "",
        layer: str = "",
        tag: str = "",
        provides: str = "",
        max_matches: int = 5,
        include_marketplace: bool = True,
    ) -> dict[str, Any]:
        self.refresh_local_index(save=False)
        local_capabilities = []
        for capability in self.capability_bus.list_capabilities():
            if layer and capability.layer.value != layer:
                continue
            if tag and tag not in capability.tags:
                continue
            if provides and provides not in capability.provides:
                continue
            local_capabilities.append(capability)
        payload = build_capability_route_projection(
            local_capabilities,
            query=query,
            provides=provides,
            tree_projection=self.capability_bus.get_tree_projection(),
            projected_runtime_view=self.capability_bus.get_context("projected_runtime_view"),
            max_matches=max_matches,
        )
        payload["filters"] = {
            "layer": layer,
            "tag": tag,
            "provides": provides,
        }
        if include_marketplace:
            marketplace = self._discover_marketplace(query=query, provides=provides)
            payload["marketplace_candidates"] = marketplace[: max(1, int(max_matches))]
            payload["marketplace_count"] = len(marketplace)
        return payload

    def _register_from_unified_inventory(self, inventory: UnifiedAssetInventoryProtocol) -> None:
        all_info = inventory.list_all()
        skill_groups: dict[str, list[str]] = {}

        for info in all_info:
            if info.layer == "skill_tool":
                skill_name = info.skill_name or "unknown"
                skill_groups.setdefault(skill_name, []).append(info.name)
            else:
                self.capability_bus.runtime.register(
                    info.name,
                    CapabilityLayer.TOOL,
                    description=info.description[:200],
                    tags=info.tags,
                    metadata=annotate_capability_tree_metadata(
                        name=info.name,
                        layer=CapabilityLayer.TOOL,
                        description=info.description[:200],
                        tags=info.tags,
                    ),
                    registered_by="unified_inventory",
                )

        if self.skill_registry is not None:
            for skill_name, tool_names in skill_groups.items():
                skill_def = self.skill_registry.get_skill(skill_name)
                description = skill_def.description if skill_def else ""
                self.capability_bus.runtime.register(
                    skill_name,
                    CapabilityLayer.SKILL,
                    description=description[:200],
                    dependencies=tool_names,
                    provides=tool_names,
                    tags=skill_def.capabilities if skill_def else [],
                    metadata=annotate_capability_tree_metadata(
                        name=skill_name,
                        layer=CapabilityLayer.SKILL,
                        description=description[:200],
                        tags=list(skill_def.capabilities if skill_def else []),
                        dependencies=tool_names,
                        provides=tool_names,
                        metadata={
                            "source_name": getattr(skill_def, "source_name", "") if skill_def else "",
                            "enabled": bool(getattr(skill_def, "enabled", True)) if skill_def else True,
                            "tool_count": len(tool_names),
                        },
                    ),
                    registered_by="unified_inventory",
                )

    def _filter_local_capabilities(
        self,
        *,
        query: str = "",
        layer: str = "",
        tag: str = "",
        provides: str = "",
        projected_runtime_view: dict[str, Any] | None = None,
    ) -> list[Capability]:
        capabilities = self.capability_bus.list_capabilities()
        query_lower = query.lower().strip()
        filtered: list[Capability] = []
        for capability in capabilities:
            if layer and capability.layer.value != layer:
                continue
            if tag and tag not in capability.tags:
                continue
            if provides and provides not in capability.provides:
                continue
            if query_lower:
                haystack = " ".join(
                    [
                        capability.name,
                        capability.description,
                        " ".join(capability.tags),
                        " ".join(capability.provides),
                        json.dumps(capability.metadata, ensure_ascii=False, default=str),
                    ]
                ).lower()
                if query_lower not in haystack:
                    continue
            filtered.append(capability)
        filtered.sort(
            key=lambda item: selection_metadata_for_capability(
                item, query=query, provides=provides, projected_runtime_view=projected_runtime_view
            )["selection_sort_key"]
        )
        return filtered

    @staticmethod
    def _serialize_capability_for_selection(
        capability: Capability,
        *,
        query: str = "",
        provides: str = "",
        projected_runtime_view: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        payload = capability.to_dict()
        selection = selection_metadata_for_capability(
            capability, query=query, provides=provides, projected_runtime_view=projected_runtime_view
        )
        payload["tree"] = dict(selection.get("tree", {}))
        payload["selection_score"] = int(selection.get("selection_score", 0) or 0)
        payload["selection_reason"] = str(selection.get("selection_reason", "")).strip()
        return payload

    def _discover_marketplace(self, *, query: str = "", provides: str = "") -> list[dict[str, Any]]:
        query_lower = (query or provides).lower().strip()
        items = self.skill_marketplace.list_available()
        if not query_lower:
            return items
        filtered = []
        for item in items:
            tags = [str(tag) for tag in item.get("tags", [])]
            haystack = " ".join(
                [
                    str(item.get("name", "")),
                    str(item.get("description", "")),
                    " ".join(tags),
                ]
            ).lower()
            if query_lower in haystack:
                filtered.append(item)
        return filtered

    def _discover_hub(
        self,
        *,
        query: str,
        hub_url: str,
        hub_token: str,
        hub_type: str,
        page: int,
    ) -> list[dict[str, Any]]:
        client = self._hub_client_factory(hub_url, hub_token or None)
        if query.strip():
            return client.search(query, pkg_type=hub_type or None, page=page)
        listing = client.list_packages(pkg_type=hub_type or None, page=page)
        return list(listing.get("items", []))

    @staticmethod
    def _build_interface_contract(capability: Capability) -> dict[str, Any]:
        interface: dict[str, Any] = {
            "kind": capability.layer.value,
            "provides": list(capability.provides),
            "tags": list(capability.tags),
            "metadata": dict(capability.metadata),
        }
        if "schema" in capability.metadata:
            interface["schema"] = capability.metadata["schema"]
        if "parameters" in capability.metadata:
            interface["parameters"] = capability.metadata["parameters"]
        return interface

    def _skill_dir(self, skill_name: str) -> Path | None:
        if self.skill_registry is not None:
            path = self.skill_registry.skill_dir(skill_name)
            if path is not None:
                return Path(path)
        candidate = Path(self.skill_marketplace.skills_dir) / skill_name
        return candidate if candidate.exists() else None

    def discover(
        self,
        *,
        query: str = "",
        layer: str = "",
        tag: str = "",
        provides: str = "",
        include_marketplace: bool = True,
        include_hub: bool = False,
        hub_url: str = "",
        hub_token: str = "",
        hub_type: str = "skill",
        page: int = 1,
    ) -> dict[str, Any]:
        self.refresh_local_index(save=False)
        projected_runtime_view = self.capability_bus.get_context("projected_runtime_view")
        local = [
            self._serialize_capability_for_selection(
                capability, query=query, provides=provides, projected_runtime_view=projected_runtime_view
            )
            for capability in self._filter_local_capabilities(
                query=query,
                layer=layer,
                tag=tag,
                provides=provides,
                projected_runtime_view=projected_runtime_view,
            )
        ]
        marketplace = self._discover_marketplace(query=query, provides=provides) if include_marketplace else []
        hub = (
            self._discover_hub(
                query=query or provides,
                hub_url=hub_url,
                hub_token=hub_token,
                hub_type=hub_type,
                page=page,
            )
            if include_hub and hub_url.strip()
            else []
        )
        payload = {
            "query": query,
            "filters": {
                "layer": layer,
                "tag": tag,
                "provides": provides,
                "include_marketplace": include_marketplace,
                "include_hub": include_hub,
            },
            "local": local,
            "marketplace": marketplace,
            "hub": hub,
            "counts": {
                "local": len(local),
                "marketplace": len(marketplace),
                "hub": len(hub),
            },
        }
        event_bus.emit(
            Event(
                type=EventType.CAPABILITY_DISCOVERED,
                source="capability_registry",
                payload={"query": query, "provides": provides, "counts": payload["counts"]},
            )
        )
        return payload

    def find_providers(self, provides: str, *, layer: str = "") -> dict[str, Any]:
        self.refresh_local_index(save=False)
        capabilities = self._filter_local_capabilities(provides=provides, layer=layer)
        return {
            "provides": provides,
            "providers": [
                self._serialize_capability_for_selection(capability, query=provides, provides=provides)
                for capability in capabilities
            ],
            "count": len(capabilities),
        }

    def get_capability_contract(self, capability_name: str) -> dict[str, Any] | None:
        self.refresh_local_index(save=False)
        capability = self.capability_bus.get(capability_name)
        if capability is None:
            return None
        tree_metadata = annotate_capability_tree_metadata(
            name=capability.name,
            layer=capability.layer,
            description=capability.description,
            tags=capability.tags,
            dependencies=capability.dependencies,
            provides=capability.provides,
            metadata=capability.metadata,
        ).get("tree", {})
        contract = {
            "id": capability.name,
            "layer": capability.layer.value,
            "description": capability.description,
            "tags": list(capability.tags),
            "provides": list(capability.provides),
            "dependencies": list(capability.dependencies),
            "metadata": dict(capability.metadata),
            "tree": dict(tree_metadata),
            "interface": self._build_interface_contract(capability),
        }
        if capability.layer == CapabilityLayer.APP and self.app_manager is not None:
            app_def = self.app_manager.get_app(capability.name)
            if app_def is not None:
                contract["app_contract"] = {
                    "mode": app_def.mode,
                    "entry_point": app_def.entry_point,
                    "api_enabled": app_def.api_enabled,
                    "require_auth": app_def.require_auth,
                    "exports": list(app_def.exports),
                    "shared_datastores": list(app_def.shared_datastores),
                    "shared_schemas": [dict(item) for item in app_def.shared_schemas],
                    "data_contracts": [dict(item) for item in app_def.data_contracts],
                }
        if capability.layer == CapabilityLayer.SKILL and self.skill_registry is not None:
            skill_def = self.skill_registry.get_skill(capability.name)
            if skill_def is not None:
                contract["skill_contract"] = {
                    "source": skill_def.source_name,
                    "enabled": skill_def.enabled,
                    "capabilities": list(skill_def.capabilities),
                    "tool_names": [tool.get("name", "") for tool in skill_def.tools],
                    "requires_bins": list(skill_def.requires_bins),
                    "requires_config": list(skill_def.requires_config),
                    "primary_env": skill_def.primary_env,
                    "openclaw_metadata": dict(skill_def.openclaw_metadata),
                }
        return contract

    def publish_skill(
        self,
        skill_name: str,
        *,
        version: str = "0.1.0",
        changelog: str = "",
        publish_to_hub: bool = False,
        hub_url: str = "",
        hub_token: str = "",
    ) -> dict[str, Any]:
        package_result = self.skill_marketplace.package_skill(skill_name)
        if not package_result.get("success"):
            return package_result
        remote_result: dict[str, Any] | None = None
        if publish_to_hub:
            skill_dir = self._skill_dir(skill_name)
            if skill_dir is None:
                return {"success": False, "error": f"Skill '{skill_name}' not found for remote publish"}
            client = self._hub_client_factory(hub_url, hub_token or None)
            remote_result = client.publish(
                str(skill_dir),
                pkg_type="skill",
                version=version,
                changelog=changelog,
            )
        self.refresh_local_index(save=True)
        payload = {
            "success": True,
            "skill_name": skill_name,
            "package": package_result,
            "remote": remote_result,
        }
        event_bus.emit(
            Event(
                type=EventType.CAPABILITY_PUBLISHED,
                source="capability_registry",
                payload={"kind": "skill", "name": skill_name, "remote": bool(remote_result), "version": version},
            )
        )
        return payload

    def install_skill(
        self,
        *,
        package_path: str = "",
        url: str = "",
        github_repo: str = "",
        github_subpath: str = "",
        hub_slug: str = "",
        version: str = "latest",
        hub_url: str = "",
        hub_token: str = "",
    ) -> dict[str, Any]:
        if hub_slug:
            if not hub_url.strip():
                return {"success": False, "error": "hub_url is required for hub installs"}
            client = self._hub_client_factory(hub_url, hub_token or None)
            installed_dir = client.install(hub_slug, version=version, target_dir=self.skill_marketplace.skills_dir)
            result = {"success": True, "skill_name": hub_slug, "path": str(installed_dir), "source": "hub"}
        elif github_repo:
            result = self.skill_marketplace.install_from_github(repo=github_repo, subpath=github_subpath)
        elif url:
            result = self.skill_marketplace.install_from_url(url=url)
        elif package_path:
            result = self.skill_marketplace.install_skill(package_path)
        else:
            result = {"success": False, "error": "Provide package_path, url, github_repo, or hub_slug"}
        if result.get("success"):
            if self.skill_registry is not None:
                self.skill_registry.reload()
            self.refresh_local_index(save=True)
            event_bus.emit(
                Event(
                    type=EventType.CAPABILITY_INSTALLED,
                    source="capability_registry",
                    payload={
                        "kind": "skill",
                        "name": result.get("skill_name", hub_slug),
                        "source": result.get("source", "marketplace"),
                    },
                )
            )
        return result

    def get_registry_snapshot(self) -> dict[str, Any]:
        capabilities = [
            self._serialize_capability_for_selection(capability)
            for capability in self._filter_local_capabilities()
        ]
        summary: dict[str, Any] = {
            "stats": self.capability_bus.get_stats(),
            "graph": self.capability_bus.get_layer_graph(),
            "tree": self.capability_bus.get_tree_projection(),
            "capabilities": capabilities,
            "marketplace": {
                "catalog": sorted(self.skill_marketplace.list_available(), key=lambda item: item.get("name", "")),
                "discovered": sorted(
                    self.skill_marketplace.discover_skills(),
                    key=lambda item: item.get("name", ""),
                ),
            },
        }
        if self.skill_registry is not None:
            summary["sources"] = self.skill_registry.list_sources()
        if self.app_manager is not None:
            apps = self.app_manager.list_apps()
            summary["apps"] = {"count": len(apps), "items": apps}
        return summary

    def route_query(
        self,
        *,
        query: str = "",
        layer: str = "",
        tag: str = "",
        provides: str = "",
        max_matches: int = 5,
        include_marketplace: bool = True,
    ) -> dict[str, Any]:
        self.refresh_local_index(save=False)
        local_capabilities = []
        for capability in self.capability_bus.list_capabilities():
            if layer and capability.layer.value != layer:
                continue
            if tag and tag not in capability.tags:
                continue
            if provides and provides not in capability.provides:
                continue
            local_capabilities.append(capability)
        payload = build_capability_route_projection(
            local_capabilities,
            query=query,
            provides=provides,
            tree_projection=self.capability_bus.get_tree_projection(),
            projected_runtime_view=self.capability_bus.get_context("projected_runtime_view"),
            max_matches=max_matches,
        )
        payload["filters"] = {
            "layer": layer,
            "tag": tag,
            "provides": provides,
        }
        if include_marketplace:
            marketplace = self._discover_marketplace(query=query, provides=provides)
            payload["marketplace_candidates"] = marketplace[: max(1, int(max_matches))]
            payload["marketplace_count"] = len(marketplace)
        return payload

    def _register_from_unified_inventory(self, inventory: UnifiedAssetInventory) -> None:
        all_info = inventory.list_all()
        skill_groups: dict[str, list[str]] = {}

        for info in all_info:
            if info.layer == "skill_tool":
                skill_name = info.skill_name or "unknown"
                skill_groups.setdefault(skill_name, []).append(info.name)
            else:
                self.capability_bus.runtime.register(
                    info.name,
                    CapabilityLayer.TOOL,
                    description=info.description[:200],
                    tags=info.tags,
                    metadata=annotate_capability_tree_metadata(
                        name=info.name,
                        layer=CapabilityLayer.TOOL,
                        description=info.description[:200],
                        tags=info.tags,
                    ),
                    registered_by="unified_inventory",
                )

        if self.skill_registry is not None:
            for skill_name, tool_names in skill_groups.items():
                skill_def = self.skill_registry.get_skill(skill_name)
                description = skill_def.description if skill_def else ""
                self.capability_bus.runtime.register(
                    skill_name,
                    CapabilityLayer.SKILL,
                    description=description[:200],
                    dependencies=tool_names,
                    provides=tool_names,
                    tags=skill_def.capabilities if skill_def else [],
                    metadata=annotate_capability_tree_metadata(
                        name=skill_name,
                        layer=CapabilityLayer.SKILL,
                        description=description[:200],
                        tags=list(skill_def.capabilities if skill_def else []),
                        dependencies=tool_names,
                        provides=tool_names,
                        metadata={
                            "source_name": getattr(skill_def, "source_name", "") if skill_def else "",
                            "enabled": bool(getattr(skill_def, "enabled", True)) if skill_def else True,
                            "tool_count": len(tool_names),
                        },
                    ),
                    registered_by="unified_inventory",
                )

    def _filter_local_capabilities(
        self,
        *,
        query: str = "",
        layer: str = "",
        tag: str = "",
        provides: str = "",
        projected_runtime_view: dict[str, Any] | None = None,
    ) -> list[Capability]:
        capabilities = self.capability_bus.list_capabilities()
        query_lower = query.lower().strip()
        filtered: list[Capability] = []
        for capability in capabilities:
            if layer and capability.layer.value != layer:
                continue
            if tag and tag not in capability.tags:
                continue
            if provides and provides not in capability.provides:
                continue
            if query_lower:
                haystack = " ".join(
                    [
                        capability.name,
                        capability.description,
                        " ".join(capability.tags),
                        " ".join(capability.provides),
                        json.dumps(capability.metadata, ensure_ascii=False, default=str),
                    ]
                ).lower()
                if query_lower not in haystack:
                    continue
            filtered.append(capability)
        filtered.sort(
            key=lambda item: selection_metadata_for_capability(
                item, query=query, provides=provides, projected_runtime_view=projected_runtime_view
            )["selection_sort_key"]
        )
        return filtered

    @staticmethod
    def _serialize_capability_for_selection(
        capability: Capability,
        *,
        query: str = "",
        provides: str = "",
        projected_runtime_view: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        payload = capability.to_dict()
        selection = selection_metadata_for_capability(
            capability, query=query, provides=provides, projected_runtime_view=projected_runtime_view
        )
        payload["tree"] = dict(selection.get("tree", {}))
        payload["selection_score"] = int(selection.get("selection_score", 0) or 0)
        payload["selection_reason"] = str(selection.get("selection_reason", "")).strip()
        return payload

    def _discover_marketplace(self, *, query: str = "", provides: str = "") -> list[dict[str, Any]]:
        query_lower = (query or provides).lower().strip()
        items = self.skill_marketplace.list_available()
        if not query_lower:
            return items
        filtered = []
        for item in items:
            tags = [str(tag) for tag in item.get("tags", [])]
            haystack = " ".join(
                [
                    str(item.get("name", "")),
                    str(item.get("description", "")),
                    " ".join(tags),
                ]
            ).lower()
            if query_lower in haystack:
                filtered.append(item)
        return filtered

    def _discover_hub(
        self,
        *,
        query: str,
        hub_url: str,
        hub_token: str,
        hub_type: str,
        page: int,
    ) -> list[dict[str, Any]]:
        client = self._hub_client_factory(hub_url, hub_token or None)
        if query.strip():
            return client.search(query, pkg_type=hub_type or None, page=page)
        listing = client.list_packages(pkg_type=hub_type or None, page=page)
        return list(listing.get("items", []))

    @staticmethod
    def _build_interface_contract(capability: Capability) -> dict[str, Any]:
        interface: dict[str, Any] = {
            "kind": capability.layer.value,
            "provides": list(capability.provides),
            "tags": list(capability.tags),
            "metadata": dict(capability.metadata),
        }
        if "schema" in capability.metadata:
            interface["schema"] = capability.metadata["schema"]
        if "parameters" in capability.metadata:
            interface["parameters"] = capability.metadata["parameters"]
        return interface

    def _skill_dir(self, skill_name: str) -> Path | None:
        if self.skill_registry is not None:
            path = self.skill_registry.skill_dir(skill_name)
            if path is not None:
                return Path(path)
        candidate = Path(self.skill_marketplace.skills_dir) / skill_name
        return candidate if candidate.exists() else None
