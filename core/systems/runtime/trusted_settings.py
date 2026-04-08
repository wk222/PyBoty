"""Canonical trusted settings stack for system/user/project/session config."""

from __future__ import annotations

import json
from copy import deepcopy
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any


class SettingsSource(str, Enum):
    SYSTEM = "system"
    USER = "user"
    PROJECT = "project"
    SESSION = "session"
    MANAGED_POLICY = "managed_policy"


SOURCE_ORDER = (
    SettingsSource.SYSTEM,
    SettingsSource.USER,
    SettingsSource.PROJECT,
    SettingsSource.SESSION,
    SettingsSource.MANAGED_POLICY,
)


DEFAULT_SETTINGS: dict[str, Any] = {
    "llm_config": {
        "provider": None,
        "api_key": None,
        "api_base": None,
        "model": "gpt-4",
        "temperature": 0.7,
    },
    "llm_fallback": [],
    "agent_config": {
        "thread_id": "default",
    },
    "observability": {
        "backend": "none",
        "langfuse_public_key": None,
        "langfuse_secret_key": None,
        "langfuse_host": None,
        "log_level": "INFO",
    },
    "rag_config": {
        "enabled": False,
        "backend": "chroma",
        "embedding_model": None,
        "embedding_batch_size": 32,
        "search_strategy": "vector",
        "hybrid_keyword_weight": 0.35,
        "hybrid_vector_weight": 0.65,
        "mmr_enabled": False,
        "mmr_lambda": 0.7,
        "temporal_decay_enabled": False,
        "temporal_half_life_days": 30.0,
        "chunk_size": 1000,
        "chunk_overlap": 200,
        "persist_dir": None,
    },
    "agent_control": {
        "mode": "balanced",
        "blocked_tools": [],
        "blocked_dynamic_tools": [],
        "risky_tools": [],
        "approval_required_tools": [],
        "approval_required_dynamic_tools": False,
        "allow_dynamic_tools": True,
        "allow_tool_mutation": True,
        "allow_agent_mutation": True,
        "allow_agent_delegation": True,
        "max_subagent_depth": 3,
        "max_concurrent_subagents": 5,
        "subagent_timeout_seconds": 300,
        "max_recent_tool_calls": 20,
        "stuck_loop_warning_threshold": 3,
        "stuck_loop_kill_threshold": 6,
    },
    "permission": {
        "mode": "default",
        "rules": {},
    },
    "policy_control": {
        "runtime_writable_sources": ["session", "managed_policy"],
        "managed_sources": ["system", "user", "project"],
    },
    "extra_skill_sources": [],
    "channels": {},
    "channel_routes": [],
    "gateway": {
        "auth": {
            "mode": "none",
            "token": None,
            "password": None,
        },
        "ws": {
            "enabled": True,
            "protocol_version": 3,
            "tick_interval_ms": 15000,
            "require_device_id": True,
            "require_paired_device_token": False,
        },
        "pairing": {
            "enabled": True,
            "auto_approve_local": True,
        },
        "http": {
            "endpoints": {
                "responses": {
                    "enabled": True,
                    "stream_enabled": True,
                },
                "models": {
                    "enabled": True,
                },
            },
        },
    },
    "openclaw_compat": {
        "repo_path": None,
        "config_path": None,
    },
}


def merge_settings(base: dict[str, Any], overlay: dict[str, Any] | None = None) -> dict[str, Any]:
    """Deep-merge two settings objects without mutating either side."""
    merged = deepcopy(base)
    if not isinstance(overlay, dict):
        return merged

    for key, value in overlay.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = merge_settings(merged[key], value)
        else:
            merged[key] = deepcopy(value)
    return merged


def load_settings_file(path: str | Path | None) -> dict[str, Any]:
    """Read one JSON settings file and require an object payload."""
    if not path:
        return {}
    resolved = Path(path)
    if not resolved.exists():
        return {}
    with resolved.open("r", encoding="utf-8") as file:
        payload = json.load(file)
    if not isinstance(payload, dict):
        raise ValueError(f"Expected object JSON in settings file: {resolved}")
    return payload


def _flatten_settings(data: dict[str, Any], *, prefix: str = "") -> dict[str, Any]:
    flattened: dict[str, Any] = {}
    for key, value in data.items():
        dotted = f"{prefix}.{key}" if prefix else str(key)
        if isinstance(value, dict):
            flattened.update(_flatten_settings(value, prefix=dotted))
        else:
            flattened[dotted] = value
    return flattened


@dataclass(frozen=True, slots=True)
class TrustedSettingsLayer:
    source: SettingsSource
    values: dict[str, Any]
    path: str = ""

    def to_projection(self) -> dict[str, Any]:
        flattened = _flatten_settings(self.values)
        return {
            "source": self.source.value,
            "path": self.path,
            "top_level_keys": sorted(self.values.keys()),
            "entry_count": len(flattened),
        }


@dataclass(frozen=True, slots=True)
class TrustedSettingsBundle:
    layers: dict[str, TrustedSettingsLayer] = field(default_factory=dict)
    effective: dict[str, Any] = field(default_factory=dict)
    provenance: dict[str, str] = field(default_factory=dict)

    def get_layer(self, source: SettingsSource | str) -> TrustedSettingsLayer | None:
        key = source.value if isinstance(source, SettingsSource) else str(source).strip().lower()
        return self.layers.get(key)

    def with_session(self, session_values: dict[str, Any] | None) -> TrustedSettingsBundle:
        return self.with_runtime_values(
            source=SettingsSource.SESSION,
            values=session_values,
            path="session://runtime",
        )

    def policy_control(self) -> dict[str, Any]:
        raw = self.effective.get("policy_control", {})
        return deepcopy(raw) if isinstance(raw, dict) else {}

    def runtime_writable_sources(self) -> set[str]:
        raw = self.policy_control().get("runtime_writable_sources", [])
        values = {str(item).strip().lower() for item in raw if str(item).strip()}
        return values or {SettingsSource.SESSION.value}

    def managed_sources(self) -> set[str]:
        raw = self.policy_control().get("managed_sources", [])
        return {str(item).strip().lower() for item in raw if str(item).strip()}

    def assert_runtime_writable(self, source: SettingsSource | str) -> str:
        normalized = source.value if isinstance(source, SettingsSource) else str(source).strip().lower()
        if not normalized:
            normalized = SettingsSource.SESSION.value
        if normalized in self.runtime_writable_sources():
            return normalized
        raise PermissionError(
            f"Settings source '{normalized}' is managed and is not runtime-writable"
        )

    def with_runtime_values(
        self,
        *,
        source: SettingsSource | str,
        values: dict[str, Any] | None,
        path: str = "",
    ) -> TrustedSettingsBundle:
        normalized_source = self.assert_runtime_writable(source)
        if not isinstance(values, dict) or not values:
            return self
        layer_source = SettingsSource(normalized_source)
        existing = self.layers.get(normalized_source)
        merged_values = merge_settings(
            dict(existing.values) if isinstance(existing, TrustedSettingsLayer) else {},
            values,
        )
        layers = dict(self.layers)
        layers[normalized_source] = TrustedSettingsLayer(
            source=layer_source,
            values=merged_values,
            path=path or (existing.path if isinstance(existing, TrustedSettingsLayer) else f"{normalized_source}://runtime"),
        )
        return _rebuild_bundle_from_layers(layers)

    def replace_runtime_values(
        self,
        *,
        source: SettingsSource | str,
        values: dict[str, Any] | None,
        path: str = "",
    ) -> TrustedSettingsBundle:
        normalized_source = self.assert_runtime_writable(source)
        layer_source = SettingsSource(normalized_source)
        existing = self.layers.get(normalized_source)
        layers = dict(self.layers)
        layers[normalized_source] = TrustedSettingsLayer(
            source=layer_source,
            values=deepcopy(values or {}),
            path=path or (existing.path if isinstance(existing, TrustedSettingsLayer) else f"{normalized_source}://runtime"),
        )
        return _rebuild_bundle_from_layers(layers)

    def with_managed_policy(
        self,
        *,
        domain: str,
        policy: dict[str, Any] | None,
        path: str = "",
    ) -> TrustedSettingsBundle:
        normalized_source = self.assert_runtime_writable(SettingsSource.MANAGED_POLICY)
        existing = self.layers.get(normalized_source)
        next_values = deepcopy(existing.values) if isinstance(existing, TrustedSettingsLayer) else {}
        domain_key = str(domain).strip()
        if not domain_key:
            raise ValueError("Managed policy domain cannot be empty")
            
        if policy is not None:
            next_values[domain_key] = deepcopy(policy)
        else:
            next_values.pop(domain_key, None)
            
        return self.replace_runtime_values(source=normalized_source, values=next_values, path=path)

    def with_agent_control_policy(
        self,
        *,
        mode: str | None = None,
        blocked_tools: list[str] | None = None,
        max_subagent_depth: int | None = None,
        max_concurrent_subagents: int | None = None,
        path: str = "",
    ) -> TrustedSettingsBundle:
        normalized_source = self.assert_runtime_writable(SettingsSource.MANAGED_POLICY)
        existing = self.layers.get(normalized_source)
        next_values = deepcopy(existing.values) if isinstance(existing, TrustedSettingsLayer) else {}
        
        control_policy: dict[str, Any] = dict(next_values.get("agent_control", {}))
        if mode is not None:
            control_policy["mode"] = str(mode).strip()
        if blocked_tools is not None:
            control_policy["blocked_tools"] = [str(t).strip() for t in blocked_tools if str(t).strip()]
        if max_subagent_depth is not None:
            control_policy["max_subagent_depth"] = max(1, int(max_subagent_depth))
        if max_concurrent_subagents is not None:
            control_policy["max_concurrent_subagents"] = max(1, int(max_concurrent_subagents))
            
        next_values["agent_control"] = control_policy
        return self.replace_runtime_values(source=normalized_source, values=next_values, path=path)

    def with_permission_source(
        self,
        *,
        source: SettingsSource | str,
        mode: str = "",
        rules: dict[str, Any] | None = None,
        path: str = "",
    ) -> TrustedSettingsBundle:
        normalized_source = self.assert_runtime_writable(source)
        existing = self.layers.get(normalized_source)
        next_values = deepcopy(existing.values) if isinstance(existing, TrustedSettingsLayer) else {}
        permission_payload: dict[str, Any] = {}
        if str(mode).strip():
            permission_payload["mode"] = str(mode).strip()
        if isinstance(rules, dict) and rules:
            permission_payload["rules"] = deepcopy(rules)
        if permission_payload:
            next_values["permission"] = permission_payload
        else:
            next_values.pop("permission", None)
        return self.replace_runtime_values(source=normalized_source, values=next_values, path=path)

    def build_projection(self) -> dict[str, Any]:
        sources: list[dict[str, Any]] = []
        active_sources: list[str] = []
        for source in SOURCE_ORDER:
            layer = self.layers.get(source.value)
            if layer is None:
                continue
            projection = layer.to_projection()
            projection["active"] = bool(layer.values)
            sources.append(projection)
            if layer.values:
                active_sources.append(source.value)

        permission_mode = ""
        permission_cfg = self.effective.get("permission", {})
        if isinstance(permission_cfg, dict):
            permission_mode = str(permission_cfg.get("mode", "")).strip()

        return {
            "summary": (
                "trusted settings: "
                + " -> ".join(active_sources or [SettingsSource.SYSTEM.value])
                + (f", permission={permission_mode}" if permission_mode else "")
            ),
            "active_sources": active_sources,
            "sources": sources,
            "paths": {
                source.value: self.layers[source.value].path
                for source in SOURCE_ORDER
                if source.value in self.layers and self.layers[source.value].path
            },
            "provenance": dict(self.provenance),
            "permission_mode": permission_mode or "default",
            "policy_control": deepcopy(self.effective.get("policy_control", {}))
            if isinstance(self.effective.get("policy_control"), dict)
            else {},
            "mutation_policy": {
                "runtime_writable_sources": sorted(self.runtime_writable_sources()),
                "managed_sources": sorted(self.managed_sources()),
            },
        }


def _rebuild_bundle_from_layers(layers: dict[str, TrustedSettingsLayer]) -> TrustedSettingsBundle:
    effective: dict[str, Any] = {}
    provenance: dict[str, str] = {}
    for source in SOURCE_ORDER:
        layer = layers.get(source.value)
        if layer and layer.values:
            effective = merge_settings(effective, layer.values)
            for dotted_key in _flatten_settings(layer.values):
                provenance[dotted_key] = source.value
    return TrustedSettingsBundle(layers=layers, effective=effective, provenance=provenance)


def build_trusted_settings_bundle(
    *,
    system_values: dict[str, Any] | None = None,
    user_values: dict[str, Any] | None = None,
    project_values: dict[str, Any] | None = None,
    session_values: dict[str, Any] | None = None,
    managed_policy_values: dict[str, Any] | None = None,
    system_path: str | Path | None = None,
    user_path: str | Path | None = None,
    project_path: str | Path | None = None,
    managed_policy_path: str | Path | None = None,
) -> TrustedSettingsBundle:
    """Build the canonical settings bundle with provenance."""
    layers = {
        SettingsSource.SYSTEM.value: TrustedSettingsLayer(
            source=SettingsSource.SYSTEM,
            values=merge_settings(DEFAULT_SETTINGS, system_values or {}),
            path=str(system_path or ""),
        ),
        SettingsSource.USER.value: TrustedSettingsLayer(
            source=SettingsSource.USER,
            values=deepcopy(user_values or {}),
            path=str(user_path or ""),
        ),
        SettingsSource.PROJECT.value: TrustedSettingsLayer(
            source=SettingsSource.PROJECT,
            values=deepcopy(project_values or {}),
            path=str(project_path or ""),
        ),
        SettingsSource.MANAGED_POLICY.value: TrustedSettingsLayer(
            source=SettingsSource.MANAGED_POLICY,
            values=deepcopy(managed_policy_values or {}),
            path=str(managed_policy_path or ""),
        ),
    }

    effective = {}
    provenance = {}
    # Iterate through all sources in order, except SESSION which is handled by with_session
    for source in SOURCE_ORDER:
        if source == SettingsSource.SESSION:
            continue
        layer = layers.get(source.value)
        if layer and layer.values:
            effective = merge_settings(effective, layer.values)
            for dotted_key in _flatten_settings(layer.values):
                provenance[dotted_key] = source.value

    bundle = TrustedSettingsBundle(layers=layers, effective=effective, provenance=provenance)
    return bundle.with_session(session_values)


__all__ = [
    "DEFAULT_SETTINGS",
    "SOURCE_ORDER",
    "SettingsSource",
    "TrustedSettingsBundle",
    "TrustedSettingsLayer",
    "build_trusted_settings_bundle",
    "load_settings_file",
    "merge_settings",
]
