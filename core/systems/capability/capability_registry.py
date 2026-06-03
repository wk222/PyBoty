"""Unified capability-registry facade over bus, marketplace, and remote hubs."""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any, TYPE_CHECKING

from langchain.tools import BaseTool
from pydantic import BaseModel, ConfigDict, Field

if TYPE_CHECKING:
    from core.systems.runtime.backend_protocol import (
        SkillMarketplaceProtocol,
        UnifiedAssetInventoryProtocol,
    )

from core.systems.integration.pyhub_client import PyHubClient
from core.systems.runtime.event_bus import event_bus

from .capability_bus import CapabilityBus
from .capability_registry_catalog import CapabilityCatalogRuntime
from .capability_registry_grants import CapabilityGrant, CapabilityGrantRuntime


class CapabilityRegistry:
    """Thin facade that delegates catalog and grant work to focused runtimes."""

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
        self.catalog_runtime = CapabilityCatalogRuntime(
            workspace_dir=self.workspace_dir,
            capability_bus=capability_bus,
            skill_marketplace=skill_marketplace,
            skill_registry=skill_registry,
            app_manager=app_manager,
            agent_storage=agent_storage,
            pyflow_engine=pyflow_engine,
            hub_client_factory=hub_client_factory,
        )
        self.grant_runtime = CapabilityGrantRuntime(
            workspace_dir=self.workspace_dir,
            capability_bus=capability_bus,
            app_manager=app_manager,
        )

    def refresh_local_index(
        self,
        *,
        tools: list[Any] | None = None,
        unified_inventory: UnifiedAssetInventoryProtocol | None = None,
        save: bool = True,
    ) -> dict[str, Any]:
        return self.catalog_runtime.refresh_local_index(
            tools=tools,
            unified_inventory=unified_inventory,
            save=save,
        )

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
        return self.catalog_runtime.discover(
            query=query,
            layer=layer,
            tag=tag,
            provides=provides,
            include_marketplace=include_marketplace,
            include_hub=include_hub,
            hub_url=hub_url,
            hub_token=hub_token,
            hub_type=hub_type,
            page=page,
        )

    def find_providers(self, provides: str, *, layer: str = "") -> dict[str, Any]:
        return self.catalog_runtime.find_providers(provides, layer=layer)

    def get_capability_contract(self, capability_name: str) -> dict[str, Any] | None:
        return self.catalog_runtime.get_capability_contract(capability_name)

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
        return self.catalog_runtime.publish_skill(
            skill_name,
            version=version,
            changelog=changelog,
            publish_to_hub=publish_to_hub,
            hub_url=hub_url,
            hub_token=hub_token,
        )

    def issue_app_grant(
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
        return self.grant_runtime.issue_app_grant(
            caller_app=caller_app,
            target_app=target_app,
            capability_name=capability_name,
            provides=provides,
            requested_quota=requested_quota,
            ttl_seconds=ttl_seconds,
            metadata=metadata,
        )

    def list_grants(self, *, caller_app: str = "", provider_app: str = "") -> list[dict[str, Any]]:
        return self.grant_runtime.list_grants(caller_app=caller_app, provider_app=provider_app)

    def invoke_app_capability(
        self,
        *,
        caller_app: str,
        grant_token: str,
        action: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        return self.grant_runtime.invoke_app_capability(
            caller_app=caller_app,
            grant_token=grant_token,
            action=action,
            payload=payload,
        )

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
        return self.catalog_runtime.install_skill(
            package_path=package_path,
            url=url,
            github_repo=github_repo,
            github_subpath=github_subpath,
            hub_slug=hub_slug,
            version=version,
            hub_url=hub_url,
            hub_token=hub_token,
        )

    def route(
        self,
        *,
        query: str = "",
        layer: str = "",
        tag: str = "",
        provides: str = "",
        max_matches: int = 5,
        include_marketplace: bool = True,
    ) -> dict[str, Any]:
        return self.catalog_runtime.route_query(
            query=query,
            layer=layer,
            tag=tag,
            provides=provides,
            max_matches=max_matches,
            include_marketplace=include_marketplace,
        )

    def get_registry_snapshot(self) -> dict[str, Any]:
        return self.catalog_runtime.get_registry_snapshot()


class CapabilityRegistryInput(BaseModel):
    action: str = Field(
        description=(
            "registry action: snapshot / discover / route / providers / contract"
            " / publish_skill / install_skill / issue_grant / list_grants / invoke"
        )
    )
    query: str = Field(default="", description="search query for discovery")
    layer: str = Field(default="", description="filter layer: tool/skill/agent/workflow/app")
    tag: str = Field(default="", description="filter tag")
    provides: str = Field(default="", description="required provided capability")
    limit: int = Field(default=5, description="max routed capability matches to return")
    name: str = Field(default="", description="capability or skill name")
    package_path: str = Field(default="", description="local .skill package path for install")
    url: str = Field(default="", description="remote package URL for install")
    github_repo: str = Field(default="", description="GitHub repo for install, e.g. owner/repo")
    github_subpath: str = Field(default="", description="optional skill subpath inside the GitHub repo")
    hub_slug: str = Field(default="", description="remote hub package slug")
    version: str = Field(default="0.1.0", description="version for publish")
    changelog: str = Field(default="", description="changelog for publish")
    publish_to_hub: bool = Field(default=False, description="publish packaged skill to remote hub")
    hub_url: str = Field(default="", description="remote hub URL")
    hub_token: str = Field(default="", description="remote hub token")
    # grant / invoke fields
    caller_app: str = Field(default="", description="calling app name for grant operations")
    target_app: str = Field(default="", description="target/provider app name for grant")
    grant_token: str = Field(default="", description="grant token for invoke")
    invoke_action: str = Field(default="", description="action to invoke on provider app")
    invoke_payload: str = Field(default="{}", description="JSON-encoded payload for invoke action")
    ttl_seconds: int = Field(default=3600, description="grant TTL in seconds")
    requested_quota: int = Field(default=1, description="quota units to request")
    model_config = ConfigDict(arbitrary_types_allowed=True)


class CapabilityRegistryTool(BaseTool):
    name: str = "capability_registry"
    description: str = (
        "统一查询和管理 PyBot 能力生态：发现本地能力、查看标准化契约、查询能力提供者，"
        "把技能打包/发布到能力市场，以及通过 zero-trust grant 机制完成 APP-to-APP 能力授权和调用。"
        " action 枚举：snapshot / discover / route / providers / contract"
        " / publish_skill / install_skill / issue_grant / list_grants / invoke"
    )
    args_schema: type[BaseModel] = CapabilityRegistryInput
    registry: Any = Field(default=None, exclude=True)
    model_config = ConfigDict(arbitrary_types_allowed=True)

    def _run(
        self,
        action: str,
        query: str = "",
        layer: str = "",
        tag: str = "",
        provides: str = "",
        limit: int = 5,
        name: str = "",
        package_path: str = "",
        url: str = "",
        github_repo: str = "",
        github_subpath: str = "",
        hub_slug: str = "",
        version: str = "0.1.0",
        changelog: str = "",
        publish_to_hub: bool = False,
        hub_url: str = "",
        hub_token: str = "",
        caller_app: str = "",
        target_app: str = "",
        grant_token: str = "",
        invoke_action: str = "",
        invoke_payload: str = "{}",
        ttl_seconds: int = 3600,
        requested_quota: int = 1,
    ) -> str:
        if action == "snapshot":
            payload = self.registry.get_registry_snapshot()
        elif action == "discover":
            payload = self.registry.discover(
                query=query,
                layer=layer,
                tag=tag,
                provides=provides,
                include_hub=bool(hub_url.strip()),
                hub_url=hub_url,
                hub_token=hub_token,
            )
        elif action == "route":
            payload = self.registry.route(
                query=query,
                layer=layer,
                tag=tag,
                provides=provides,
                max_matches=limit,
            )
        elif action == "providers":
            payload = self.registry.find_providers(provides or query, layer=layer)
        elif action == "contract":
            payload = self.registry.get_capability_contract(name)
        elif action == "publish_skill":
            payload = self.registry.publish_skill(
                name,
                version=version,
                changelog=changelog,
                publish_to_hub=publish_to_hub,
                hub_url=hub_url,
                hub_token=hub_token,
            )
        elif action == "install_skill":
            payload = self.registry.install_skill(
                package_path=package_path,
                url=url,
                github_repo=github_repo,
                github_subpath=github_subpath,
                hub_slug=hub_slug,
                version=version,
                hub_url=hub_url,
                hub_token=hub_token,
            )
        elif action == "issue_grant":
            payload = self.registry.issue_app_grant(
                caller_app=caller_app,
                target_app=target_app,
                capability_name=name,
                provides=provides,
                requested_quota=requested_quota,
                ttl_seconds=ttl_seconds,
            )
        elif action == "list_grants":
            payload = {
                "grants": self.registry.list_grants(
                    caller_app=caller_app,
                    provider_app=target_app,
                )
            }
        elif action == "invoke":
            try:
                parsed_payload = json.loads(invoke_payload or "{}")
            except (json.JSONDecodeError, ValueError):
                parsed_payload = {}
            payload = self.registry.invoke_app_capability(
                caller_app=caller_app,
                grant_token=grant_token,
                action=invoke_action,
                payload=parsed_payload,
            )
        else:
            payload = {"error": f"unknown action: {action}"}
        return json.dumps(payload, ensure_ascii=False, indent=2, default=str)


def get_capability_registry_tools(registry: CapabilityRegistry) -> list[BaseTool]:
    return [CapabilityRegistryTool(registry=registry)]
