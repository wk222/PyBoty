"""Grant/invocation runtime behind the capability registry facade."""

from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from core.systems.runtime.event_bus import Event, EventType, event_bus

from .capability_bus import CapabilityBus
from .capability_bus_models import CapabilityLayer
from .capability_reporting import CapabilityBusReporter


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


class CapabilityGrantRuntime:
    """Own app-to-app grants, validation, persistence, and invocation recording."""

    def __init__(
        self,
        *,
        workspace_dir: str | Path,
        capability_bus: CapabilityBus,
        app_manager: Any | None = None,
    ) -> None:
        self.workspace_dir = Path(workspace_dir)
        self.capability_bus = capability_bus
        self._reporter = CapabilityBusReporter(capability_bus)
        self.app_manager = app_manager
        self._grants_path = self.workspace_dir / "data" / "capability_grants.json"
        self._grants: dict[str, CapabilityGrant] = self._load_grants()

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
        started_at = time.perf_counter()
        validation = self._validate_grant(grant, caller_app=caller_app)
        if validation is not None:
            return self._record_app_invocation(
                grant=grant,
                caller_app=caller_app,
                action=action,
                payload=payload,
                started_at=started_at,
                response=validation,
            )
        policy_validation = self._validate_provider_policy(grant, action=action, payload=payload)
        if policy_validation is not None:
            return self._record_app_invocation(
                grant=grant,
                caller_app=caller_app,
                action=action,
                payload=payload,
                started_at=started_at,
                response=policy_validation,
            )
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
        response = {
            "success": bool(result.get("success")),
            "provider_app": grant.provider_app,
            "capability_name": grant.capability_name,
            "quota_remaining": grant.quota_remaining,
            "result": result.get("result"),
            "error": result.get("error", ""),
        }
        return self._record_app_invocation(
            grant=grant,
            caller_app=caller_app,
            action=action,
            payload=payload,
            started_at=started_at,
            response=response,
        )

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
        matches = self.capability_bus.find(layer=CapabilityLayer.APP, provides=lookup)
        if not matches:
            return {"success": False, "error": f"No app provider found for capability '{lookup}'"}
        return {
            "success": True,
            "provider_app": matches[0].name,
            "capability_name": lookup,
        }

    @staticmethod
    def _build_caller_identity(caller: Any) -> dict[str, Any]:
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

    @staticmethod
    def _validate_grant(grant: CapabilityGrant, *, caller_app: str) -> dict[str, Any] | None:
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

    @staticmethod
    def _validate_provider_policy(
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

    def _record_app_invocation(
        self,
        *,
        grant: CapabilityGrant,
        caller_app: str,
        action: str,
        payload: dict[str, Any],
        started_at: float,
        response: dict[str, Any],
    ) -> dict[str, Any]:
        duration_ms = (time.perf_counter() - started_at) * 1000
        payload_size = len(json.dumps(payload, ensure_ascii=False, default=str).encode("utf-8"))
        self._reporter.record_capability_invocation(
            name=grant.provider_app,
            success=bool(response.get("success")),
            duration_ms=duration_ms,
            source="capability_registry",
            layer=CapabilityLayer.APP.value,
            operation=action,
            metadata={
                "caller_app": caller_app,
                "provider_app": grant.provider_app,
                "capability_name": grant.capability_name,
                "quota_remaining": grant.quota_remaining,
                "payload_bytes": payload_size,
                "error": str(response.get("error", "")),
            },
        )
        return response

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
