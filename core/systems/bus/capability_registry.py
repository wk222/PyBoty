"""Unified capability-registry façade over bus, marketplace, and remote hubs."""

from __future__ import annotations

import json
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from langchain.tools import BaseTool
from pydantic import BaseModel, ConfigDict, Field

from core.assets.skills.skill_marketplace import SkillMarketplace
from core.systems.integration.pyhub_client import PyHubClient
from core.systems.runtime.event_bus import Event, EventType, event_bus

from .capability_bus import CapabilityBus
from .capability_bus_models import Capability, CapabilityLayer


class CapabilityRegistry:
    """Coordinate local capability discovery, packaging, and remote exchange."""

    def __init__(
        self,
        *,
        workspace_dir: str | Path,
        capability_bus: CapabilityBus,
        skill_marketplace: SkillMarketplace,
        skill_registry: Any | None = None,
        app_manager: Any | None = None,
        agent_storage: Any | None = None,
        pyflow_engine: Any | None = None,
        hub_client_factory: Callable[[str, str | None], PyHubClient] | None = None,
    ) -> None:
        self.workspace_dir = Path(workspace_dir)
        self._grants_path = self.workspace_dir / "data" / "capability_grants.json"
        self.capability_bus = capability_bus
        self.skill_marketplace = skill_marketplace
        self.skill_registry = skill_registry
        self.app_manager = app_manager
        self.agent_storage = agent_storage
        self.pyflow_engine = pyflow_engine
        self._hub_client_factory = hub_client_factory or (lambda url, token=None: PyHubClient(url, api_key=token))
        self._grants: dict[str, CapabilityGrant] = self._load_grants()

    def refresh_local_index(self, *, tools: list[Any] | None = None, save: bool = True) -> dict[str, Any]:
        if tools:
            self.capability_bus.auto_register_tools(tools)
        if self.pyflow_engine is not None:
            self.capability_bus.auto_register_workflows(self.pyflow_engine)
        if self.app_manager is not None:
            self.capability_bus.auto_register_apps(self.app_manager)
        if self.skill_registry is not None:
            self.capability_bus.auto_register_skills(self.skill_registry)
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
        local = [
            capability.to_dict()
            for capability in self._filter_local_capabilities(
                query=query,
                layer=layer,
                tag=tag,
                provides=provides,
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
            "providers": [capability.to_dict() for capability in capabilities],
            "count": len(capabilities),
        }

    def get_capability_contract(self, capability_name: str) -> dict[str, Any] | None:
        self.refresh_local_index(save=False)
        capability = self.capability_bus.get(capability_name)
        if capability is None:
            return None
        contract = {
            "id": capability.name,
            "layer": capability.layer.value,
            "description": capability.description,
            "tags": list(capability.tags),
            "provides": list(capability.provides),
            "dependencies": list(capability.dependencies),
            "metadata": dict(capability.metadata),
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
        if self.app_manager is None:
            return {"success": False, "error": "App manager unavailable"}
        caller = self.app_manager.get_app(caller_app)
        if caller is None:
            return {"success": False, "error": f"Caller app '{caller_app}' not found"}
        resolved = self._resolve_app_provider(
            target_app=target_app,
            capability_name=capability_name,
            provides=provides,
        )
        if not resolved.get("success"):
            return resolved
        grant = CapabilityGrant(
            grant_id=uuid.uuid4().hex,
            token=uuid.uuid4().hex,
            caller_app=caller_app,
            provider_app=str(resolved["provider_app"]),
            capability_name=str(resolved["capability_name"]),
            quota_total=max(1, int(requested_quota)),
            quota_remaining=max(1, int(requested_quota)),
            caller_identity=self._build_caller_identity(caller),
            provider_policy=self._build_provider_policy(
                str(resolved["provider_app"]),
                metadata=dict(metadata or {}),
            ),
            metadata=dict(metadata or {}),
            expires_at=time.time() + max(60, int(ttl_seconds)),
        )
        self._grants[grant.token] = grant
        self._save_grants()
        event_bus.emit(
            Event(
                type=EventType.CAPABILITY_GRANTED,
                source="capability_registry",
                payload=grant.to_public_dict(),
            )
        )
        return {
            "success": True,
            "grant": grant.to_public_dict(),
        }

    def list_grants(self, *, caller_app: str = "", provider_app: str = "") -> list[dict[str, Any]]:
        now = time.time()
        grants = []
        for grant in self._grants.values():
            if caller_app and grant.caller_app != caller_app:
                continue
            if provider_app and grant.provider_app != provider_app:
                continue
            payload = grant.to_public_dict()
            payload["expired"] = grant.expires_at <= now
            grants.append(payload)
        grants.sort(key=lambda item: (item["provider_app"], item["caller_app"], item["capability_name"]))
        return grants

    def invoke_app_capability(
        self,
        *,
        caller_app: str,
        grant_token: str,
        action: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        if self.app_manager is None:
            return {"success": False, "error": "App manager unavailable"}
        grant = self._grants.get(grant_token)
        if grant is None:
            return {"success": False, "error": "Capability grant not found"}
        validation = self._validate_grant(grant, caller_app=caller_app)
        if validation is not None:
            return validation
        policy_validation = self._validate_provider_policy(grant, action=action, payload=payload)
        if policy_validation is not None:
            return policy_validation
        result = self.app_manager.execute_app_api(grant.provider_app, action, payload)
        grant.quota_remaining = max(0, grant.quota_remaining - 1)
        grant.last_used_at = time.time()
        self._save_grants()
        event_bus.emit(
            Event(
                type=EventType.CAPABILITY_CONSUMED,
                source="capability_registry",
                payload={
                    "caller_app": caller_app,
                    "provider_app": grant.provider_app,
                    "capability_name": grant.capability_name,
                    "action": action,
                    "quota_remaining": grant.quota_remaining,
                    "success": bool(result.get("success")),
                },
            )
        )
        return {
            "success": bool(result.get("success")),
            "provider_app": grant.provider_app,
            "capability_name": grant.capability_name,
            "quota_remaining": grant.quota_remaining,
            "result": result.get("result"),
            "error": result.get("error", ""),
        }

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
        capabilities = [capability.to_dict() for capability in self.capability_bus.list_capabilities()]
        capabilities.sort(key=lambda item: (item["layer"], item["name"]))
        summary: dict[str, Any] = {
            "stats": self.capability_bus.get_stats(),
            "graph": self.capability_bus.get_layer_graph(),
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

    def _filter_local_capabilities(
        self,
        *,
        query: str = "",
        layer: str = "",
        tag: str = "",
        provides: str = "",
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
        filtered.sort(key=lambda item: (item.layer.value, item.name))
        return filtered

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

    def _build_interface_contract(self, capability: Capability) -> dict[str, Any]:
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

    def _resolve_app_provider(
        self,
        *,
        target_app: str = "",
        capability_name: str = "",
        provides: str = "",
    ) -> dict[str, Any]:
        if self.app_manager is None:
            return {"success": False, "error": "App manager unavailable"}
        if target_app:
            app_def = self.app_manager.get_app(target_app)
            if app_def is None:
                return {"success": False, "error": f"Target app '{target_app}' not found"}
            resolved_capability = capability_name or provides or target_app
            if app_def.exports and resolved_capability not in app_def.exports and provides:
                return {
                    "success": False,
                    "error": f"App '{target_app}' does not export capability '{provides}'",
                }
            return {
                "success": True,
                "provider_app": target_app,
                "capability_name": resolved_capability,
            }
        lookup = capability_name or provides
        if not lookup:
            return {"success": False, "error": "target_app, capability_name, or provides is required"}
        matches = self._filter_local_capabilities(layer="app", provides=lookup)
        if not matches:
            return {"success": False, "error": f"No app provider found for capability '{lookup}'"}
        return {
            "success": True,
            "provider_app": matches[0].name,
            "capability_name": lookup,
        }

    def _build_caller_identity(self, caller: Any) -> dict[str, Any]:
        return {
            "app_name": caller.name,
            "mode": caller.mode,
            "agent_binding": caller.agent_binding,
            "workflow_binding": caller.workflow_binding,
            "isolated_storage": caller.isolated_storage,
            "isolated_knowledge": caller.isolated_knowledge,
        }

    def _build_provider_policy(
        self,
        provider_app: str,
        *,
        metadata: dict[str, Any],
    ) -> dict[str, Any]:
        if self.app_manager is None:
            return {}
        provider = self.app_manager.get_app(provider_app)
        if provider is None:
            return {}

        requested_policy = metadata.get("provider_policy", {})
        if not isinstance(requested_policy, dict):
            requested_policy = {}

        allowed_actions = requested_policy.get("allowed_actions", metadata.get("allowed_actions", []))
        payload_limit = requested_policy.get(
            "max_payload_bytes",
            metadata.get("max_payload_bytes", 32768 if provider.require_auth else 65536),
        )

        return {
            "require_auth": bool(provider.require_auth),
            "allowed_actions": [str(item).strip() for item in allowed_actions if str(item).strip()],
            "max_payload_bytes": max(1, int(payload_limit)),
            "exports": list(provider.exports),
            "api_enabled": bool(provider.api_enabled),
        }

    def _validate_grant(self, grant: CapabilityGrant, *, caller_app: str) -> dict[str, Any] | None:
        now = time.time()
        if grant.caller_app != caller_app:
            return {"success": False, "error": "Capability grant does not belong to caller"}
        if grant.caller_identity.get("app_name") != caller_app:
            return {"success": False, "error": "Capability grant caller identity mismatch"}
        if grant.expires_at <= now:
            return {"success": False, "error": "Capability grant expired"}
        if grant.quota_remaining <= 0:
            return {"success": False, "error": "Capability grant quota exhausted"}
        return None

    def _validate_provider_policy(
        self,
        grant: CapabilityGrant,
        *,
        action: str,
        payload: dict[str, Any],
    ) -> dict[str, Any] | None:
        policy = grant.provider_policy
        if policy.get("require_auth") and not grant.caller_identity:
            return {"success": False, "error": "Capability grant missing caller identity"}

        allowed_actions = policy.get("allowed_actions", [])
        if isinstance(allowed_actions, list) and allowed_actions and action not in allowed_actions:
            return {
                "success": False,
                "error": f"Action '{action}' is not permitted by provider policy",
            }

        max_payload_bytes = int(policy.get("max_payload_bytes", 0) or 0)
        if max_payload_bytes > 0:
            payload_size = len(json.dumps(payload, ensure_ascii=False, default=str).encode("utf-8"))
            if payload_size > max_payload_bytes:
                return {
                    "success": False,
                    "error": f"Payload exceeds provider policy limit ({max_payload_bytes} bytes)",
                }
        return None

    def _load_grants(self) -> dict[str, CapabilityGrant]:
        if not self._grants_path.exists():
            return {}
        try:
            raw = json.loads(self._grants_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        items = raw.get("grants", [])
        grants: dict[str, CapabilityGrant] = {}
        for item in items:
            if not isinstance(item, dict):
                continue
            grant = CapabilityGrant.from_dict(item)
            grants[grant.token] = grant
        return grants

    def _save_grants(self) -> None:
        payload = {
            "version": "1.0",
            "saved_at": time.time(),
            "grants": [grant.to_dict() for grant in self._grants.values()],
        }
        self._grants_path.parent.mkdir(parents=True, exist_ok=True)
        temp_path = self._grants_path.with_suffix(".tmp")
        temp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        temp_path.replace(self._grants_path)


@dataclass
class CapabilityGrant:
    grant_id: str
    token: str
    caller_app: str
    provider_app: str
    capability_name: str
    quota_total: int
    quota_remaining: int
    caller_identity: dict[str, Any]
    provider_policy: dict[str, Any]
    metadata: dict[str, Any]
    expires_at: float
    issued_at: float = 0.0
    last_used_at: float = 0.0

    def __post_init__(self) -> None:
        if not self.issued_at:
            self.issued_at = time.time()

    def to_dict(self) -> dict[str, Any]:
        return {
            "grant_id": self.grant_id,
            "token": self.token,
            "caller_app": self.caller_app,
            "provider_app": self.provider_app,
            "capability_name": self.capability_name,
            "quota_total": self.quota_total,
            "quota_remaining": self.quota_remaining,
            "caller_identity": self.caller_identity,
            "provider_policy": self.provider_policy,
            "metadata": self.metadata,
            "expires_at": self.expires_at,
            "issued_at": self.issued_at,
            "last_used_at": self.last_used_at,
        }

    def to_public_dict(self) -> dict[str, Any]:
        return {
            "grant_id": self.grant_id,
            "token": self.token,
            "caller_app": self.caller_app,
            "provider_app": self.provider_app,
            "capability_name": self.capability_name,
            "quota_total": self.quota_total,
            "quota_remaining": self.quota_remaining,
            "caller_identity": self.caller_identity,
            "provider_policy": self.provider_policy,
            "metadata": self.metadata,
            "expires_at": self.expires_at,
            "issued_at": self.issued_at,
            "last_used_at": self.last_used_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> CapabilityGrant:
        return cls(
            grant_id=str(data.get("grant_id", "")),
            token=str(data.get("token", "")),
            caller_app=str(data.get("caller_app", "")),
            provider_app=str(data.get("provider_app", "")),
            capability_name=str(data.get("capability_name", "")),
            quota_total=int(data.get("quota_total", 0)),
            quota_remaining=int(data.get("quota_remaining", 0)),
            caller_identity=dict(data.get("caller_identity", {})),
            provider_policy=dict(data.get("provider_policy", {})),
            metadata=dict(data.get("metadata", {})),
            expires_at=float(data.get("expires_at", 0)),
            issued_at=float(data.get("issued_at", 0)),
            last_used_at=float(data.get("last_used_at", 0)),
        )


class CapabilityRegistryInput(BaseModel):
    action: str = Field(description="registry action: snapshot / discover / providers / contract / publish_skill")
    query: str = Field(default="", description="search query for discovery")
    layer: str = Field(default="", description="filter layer: tool/skill/agent/workflow/app")
    tag: str = Field(default="", description="filter tag")
    provides: str = Field(default="", description="required provided capability")
    name: str = Field(default="", description="capability or skill name")
    version: str = Field(default="0.1.0", description="version for publish")
    changelog: str = Field(default="", description="changelog for publish")
    publish_to_hub: bool = Field(default=False, description="publish packaged skill to remote hub")
    hub_url: str = Field(default="", description="remote hub URL")
    hub_token: str = Field(default="", description="remote hub token")
    model_config = ConfigDict(arbitrary_types_allowed=True)


class CapabilityRegistryTool(BaseTool):
    name: str = "capability_registry"
    description: str = (
        "统一查询和管理 PyBot 能力生态：发现本地能力、查看标准化契约、查询能力提供者，并把技能打包/发布到能力市场。"
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
        name: str = "",
        version: str = "0.1.0",
        changelog: str = "",
        publish_to_hub: bool = False,
        hub_url: str = "",
        hub_token: str = "",
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
        else:
            payload = {"error": f"unknown action: {action}"}
        return json.dumps(payload, ensure_ascii=False, indent=2, default=str)


def get_capability_registry_tools(registry: CapabilityRegistry) -> list[BaseTool]:
    return [CapabilityRegistryTool(registry=registry)]
