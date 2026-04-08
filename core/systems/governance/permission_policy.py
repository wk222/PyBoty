"""Canonical permission control plane for tool governance and recovery."""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from enum import Enum
from typing import Any

logger = logging.getLogger(__name__)


class PermissionMode(str, Enum):
    DEFAULT = "default"
    PLAN = "plan"
    BYPASS = "bypass"


class RuleVerdict(str, Enum):
    ALLOW = "allow"
    DENY = "deny"
    ASK = "ask"


class PolicySource(str, Enum):
    SYSTEM = "system"
    USER = "user"
    PROJECT = "project"
    SESSION = "session"
    MANAGED_POLICY = "managed_policy"


_SOURCE_PRECEDENCE = (
    PolicySource.MANAGED_POLICY,
    PolicySource.SESSION,
    PolicySource.PROJECT,
    PolicySource.USER,
    PolicySource.SYSTEM,
)

_WRITE_TOOLS = frozenset(
    {
        "write_file",
        "str_replace",
        "bash",
        "set_permission_mode",
        "set_permission_rule",
        "remove_permission_rule",
        "clear_permission_rules",
    }
)

_MAX_POLICY_EVENTS = 32


@dataclass(slots=True)
class PermissionRule:
    tool_name: str
    verdict: RuleVerdict
    reason: str = ""
    source: str = PolicySource.SESSION.value


class PermissionControlPlane:
    """Single owner for permission modes, rules, audit events, and settings provenance."""

    def __init__(self) -> None:
        self._mode_by_source: dict[str, PermissionMode | None] = {
            source.value: None for source in _SOURCE_PRECEDENCE
        }
        self._rules_by_source: dict[str, dict[str, PermissionRule]] = {
            source.value: {} for source in _SOURCE_PRECEDENCE
        }
        self._recent_events: list[dict[str, Any]] = []
        self._policy_sources: dict[str, dict[str, Any]] = {}
        self._runtime_writable_sources: set[str] = {source.value for source in _SOURCE_PRECEDENCE}
        self._managed_sources: set[str] = set()

    @property
    def mode(self) -> PermissionMode:
        return self._resolve_mode()[0]

    @property
    def mode_source(self) -> str:
        return self._resolve_mode()[1]

    @classmethod
    def from_trusted_settings(cls, settings: Any | None) -> PermissionControlPlane:
        plane = cls()
        if settings is not None:
            plane.load_trusted_settings(settings)
        return plane

    def set_mode(self, mode: PermissionMode | str, *, source: str = PolicySource.SESSION.value) -> None:
        resolved_mode = mode if isinstance(mode, PermissionMode) else PermissionMode(str(mode).strip().lower())
        resolved_source = self._normalize_source(source)
        self._assert_runtime_writable(resolved_source)
        previous_mode, previous_source = self._resolve_mode()
        previous_raw = self._mode_by_source.get(resolved_source)
        self._mode_by_source[resolved_source] = resolved_mode
        current_mode, current_source = self._resolve_mode()
        if previous_raw != resolved_mode or previous_mode != current_mode or previous_source != current_source:
            logger.info("Permission mode changed: %s -> %s", previous_mode.value, current_mode.value)
            self._record_event(
                action="set_mode",
                mode=current_mode.value,
                previous_mode=previous_mode.value,
                source=resolved_source,
                effective_source=current_source,
            )

    def add_rule(
        self,
        tool_name: str,
        verdict: RuleVerdict | str,
        reason: str = "",
        source: str = PolicySource.SESSION.value,
        *,
        _managed: bool = False,
    ) -> None:
        normalized_tool = str(tool_name).strip()
        if not normalized_tool:
            raise ValueError("tool_name is required")
        resolved_verdict = verdict if isinstance(verdict, RuleVerdict) else RuleVerdict(str(verdict).strip().lower())
        resolved_source = self._normalize_source(source)
        if not _managed:
            self._assert_runtime_writable(resolved_source)
        self._rules_by_source.setdefault(resolved_source, {})[normalized_tool] = PermissionRule(
            tool_name=normalized_tool,
            verdict=resolved_verdict,
            reason=str(reason).strip(),
            source=resolved_source,
        )
        self._record_event(
            action="set_rule",
            tool_name=normalized_tool,
            verdict=resolved_verdict.value,
            reason=str(reason).strip(),
            source=resolved_source,
        )

    def replace_rules(
        self,
        rules: dict[str, Any] | list[Any] | None,
        *,
        source: str,
    ) -> None:
        resolved_source = self._normalize_source(source)
        self._rules_by_source[resolved_source] = {}
        for payload in self._iter_rule_payloads(rules):
            self.add_rule(
                tool_name=payload["tool_name"],
                verdict=payload["verdict"],
                reason=payload.get("reason", ""),
                source=resolved_source,
                _managed=True,
            )

    def remove_rule(self, tool_name: str, *, source: str | None = None) -> bool:
        normalized_tool = str(tool_name).strip()
        if not normalized_tool:
            return False
        sources = [self._normalize_source(source)] if source else [item.value for item in _SOURCE_PRECEDENCE]
        for source_name in sources:
            self._assert_runtime_writable(source_name)
            removed = self._rules_by_source.get(source_name, {}).pop(normalized_tool, None)
            if removed is None:
                continue
            self._record_event(
                action="remove_rule",
                tool_name=normalized_tool,
                verdict=removed.verdict.value,
                reason=removed.reason,
                source=source_name,
            )
            return True
        return False

    def clear_rules(self, *, source: str = PolicySource.SESSION.value) -> None:
        resolved_source = self._normalize_source(source)
        self._assert_runtime_writable(resolved_source)
        cleared_count = len(self._rules_by_source.get(resolved_source, {}))
        self._rules_by_source[resolved_source] = {}
        if cleared_count:
            self._record_event(
                action="clear_rules",
                source=resolved_source,
                rules_cleared=cleared_count,
            )

    def load_trusted_settings(self, settings: Any) -> None:
        projection = settings.build_projection()
        policy_control = projection.get("policy_control", {}) if isinstance(projection.get("policy_control"), dict) else {}
        runtime_writable = policy_control.get("runtime_writable_sources", [])
        managed_sources = policy_control.get("managed_sources", [])
        self._runtime_writable_sources = {
            self._normalize_source(item)
            for item in runtime_writable
            if str(item).strip()
        } or {PolicySource.SESSION.value}
        self._managed_sources = {
            self._normalize_source(item)
            for item in managed_sources
            if str(item).strip()
        }
        self._policy_sources = {
            source.value: {
                "path": projection.get("paths", {}).get(source.value, ""),
                "active": False,
                "mode": "",
                "rule_count": 0,
            }
            for source in (PolicySource.SYSTEM, PolicySource.USER, PolicySource.PROJECT)
        }

        for source in (PolicySource.SYSTEM, PolicySource.USER, PolicySource.PROJECT):
            layer = settings.get_layer(source.value)
            permission_cfg = {}
            if layer is not None and isinstance(layer.values, dict):
                permission_cfg = layer.values.get("permission", {})
            self.apply_source_settings(permission_cfg, source=source.value)

    def apply_source_settings(self, permission_cfg: Any, *, source: str) -> None:
        resolved_source = self._normalize_source(source)
        cfg = dict(permission_cfg) if isinstance(permission_cfg, dict) else {}
        raw_mode = str(cfg.get("mode", "")).strip().lower()
        self._mode_by_source[resolved_source] = PermissionMode(raw_mode) if raw_mode else None
        self.replace_rules(cfg.get("rules"), source=resolved_source)

        source_projection = self._policy_sources.setdefault(resolved_source, {})
        source_projection["active"] = bool(raw_mode or self._rules_by_source.get(resolved_source))
        source_projection["mode"] = raw_mode or ""
        source_projection["rule_count"] = len(self._rules_by_source.get(resolved_source, {}))

        self._record_event(
            action="sync_policy_source",
            source=resolved_source,
            mode=raw_mode or "",
            rule_count=len(self._rules_by_source.get(resolved_source, {})),
        )

    def evaluate(self, tool_name: str, tool_risk: str = "low") -> RuleVerdict:
        rule = self._resolve_rule(tool_name)
        if rule is not None:
            return rule.verdict

        if self.mode == PermissionMode.PLAN:
            return RuleVerdict.DENY if tool_name in _WRITE_TOOLS else RuleVerdict.ALLOW

        if self.mode == PermissionMode.BYPASS:
            return RuleVerdict.ALLOW

        return RuleVerdict.ASK if tool_risk in {"high", "critical"} or tool_name in _WRITE_TOOLS else RuleVerdict.ALLOW

    def record_runtime_decision(
        self,
        *,
        tool_name: str,
        verdict: RuleVerdict | str,
        tool_risk: str = "",
        reason: str = "",
        source: str = "runtime",
    ) -> None:
        resolved_verdict = verdict if isinstance(verdict, RuleVerdict) else RuleVerdict(str(verdict).strip().lower())
        self._record_event(
            action="evaluate",
            tool_name=str(tool_name).strip(),
            verdict=resolved_verdict.value,
            tool_risk=str(tool_risk).strip(),
            reason=str(reason).strip(),
            source=str(source).strip() or "runtime",
        )

    def build_projection(self, *, limit: int = 8) -> dict[str, Any]:
        capped_limit = max(1, int(limit or 0))
        rules = [
            {
                "tool_name": rule.tool_name,
                "verdict": rule.verdict.value,
                "reason": rule.reason,
                "source": rule.source,
            }
            for rule in self._iter_rules()
        ]
        summary = f"mode={self.mode.value}, {len(rules)} active rule{'s' if len(rules) != 1 else ''}"
        return {
            "mode": self.mode.value,
            "mode_source": self.mode_source,
            "mode_layers": {
                source.value: self._mode_by_source.get(source.value).value
                if self._mode_by_source.get(source.value) is not None
                else ""
                for source in _SOURCE_PRECEDENCE
            },
            "summary": summary,
            "rules": rules[:capped_limit],
            "recent_events": list(self._recent_events[-capped_limit:]),
            "write_tools": sorted(_WRITE_TOOLS),
            "rule_count": len(rules),
            "sources": {
                source.value: [
                    {
                        "tool_name": rule.tool_name,
                        "verdict": rule.verdict.value,
                        "reason": rule.reason,
                    }
                    for rule in self._rules_by_source.get(source.value, {}).values()
                ]
                for source in _SOURCE_PRECEDENCE
            },
            "policy_sources": dict(self._policy_sources),
            "mutation_policy": {
                "runtime_writable_sources": sorted(self._runtime_writable_sources),
                "managed_sources": sorted(self._managed_sources),
            },
        }

    def get_snapshot(self) -> dict[str, Any]:
        projection = self.build_projection(limit=32)
        return {
            "mode": projection["mode"],
            "mode_source": projection["mode_source"],
            "mode_layers": projection["mode_layers"],
            "rules": {
                rule["tool_name"]: {
                    "verdict": rule["verdict"],
                    "reason": rule["reason"],
                    "source": rule["source"],
                }
                for rule in projection["rules"]
            },
            "write_tools": projection["write_tools"],
            "recent_events": projection["recent_events"],
            "rule_count": projection["rule_count"],
            "summary": projection["summary"],
            "sources": projection["sources"],
            "policy_sources": projection["policy_sources"],
            "mutation_policy": projection["mutation_policy"],
        }

    def export_source_settings(self, *, source: str = PolicySource.SESSION.value) -> dict[str, Any]:
        resolved_source = self._normalize_source(source)
        mode = self._mode_by_source.get(resolved_source)
        rules = self._rules_by_source.get(resolved_source, {})
        permission_payload: dict[str, Any] = {"rules": {}}
        if mode is not None:
            permission_payload["mode"] = mode.value
        for tool_name, rule in sorted(rules.items()):
            permission_payload["rules"][tool_name] = {
                "verdict": rule.verdict.value,
                "reason": rule.reason,
            }
        return {"permission": permission_payload}

    def _resolve_rule(self, tool_name: str) -> PermissionRule | None:
        normalized_tool = str(tool_name).strip()
        if not normalized_tool:
            return None
        for source in _SOURCE_PRECEDENCE:
            rule = self._rules_by_source.get(source.value, {}).get(normalized_tool)
            if rule is not None:
                return rule
        return None

    def _resolve_mode(self) -> tuple[PermissionMode, str]:
        for source in _SOURCE_PRECEDENCE:
            mode = self._mode_by_source.get(source.value)
            if mode is not None:
                return mode, source.value
        return PermissionMode.DEFAULT, PolicySource.SYSTEM.value

    def _iter_rules(self) -> list[PermissionRule]:
        ordered: dict[str, PermissionRule] = {}
        for source in reversed(_SOURCE_PRECEDENCE):
            for tool_name, rule in self._rules_by_source.get(source.value, {}).items():
                ordered[tool_name] = rule
        return [ordered[name] for name in sorted(ordered)]

    def _record_event(self, *, action: str, **payload: Any) -> None:
        event = {
            "event_id": f"perm:{int(time.time() * 1000)}:{len(self._recent_events)}",
            "timestamp": time.time(),
            "action": str(action).strip(),
        }
        for key, value in payload.items():
            if value in (None, "", []):
                continue
            event[key] = value
        self._recent_events.append(event)
        self._recent_events = self._recent_events[-_MAX_POLICY_EVENTS:]

    @staticmethod
    def _normalize_source(source: str | PolicySource | None) -> str:
        if isinstance(source, PolicySource):
            return source.value
        normalized = str(source or PolicySource.SESSION.value).strip().lower()
        return PolicySource(normalized).value

    def _assert_runtime_writable(self, source: str) -> None:
        resolved_source = self._normalize_source(source)
        if resolved_source in self._runtime_writable_sources:
            return
        raise PermissionError(
            f"Permission source '{resolved_source}' is managed by trusted settings and is not runtime-writable"
        )

    @staticmethod
    def _iter_rule_payloads(rules: dict[str, Any] | list[Any] | None) -> list[dict[str, str]]:
        normalized: list[dict[str, str]] = []
        if isinstance(rules, dict):
            for tool_name, raw in rules.items():
                if isinstance(raw, dict):
                    verdict = str(raw.get("verdict", "")).strip().lower()
                    reason = str(raw.get("reason", "")).strip()
                else:
                    verdict = str(raw).strip().lower()
                    reason = ""
                if tool_name and verdict:
                    normalized.append(
                        {
                            "tool_name": str(tool_name).strip(),
                            "verdict": verdict,
                            "reason": reason,
                        }
                    )
            return normalized

        if isinstance(rules, list):
            for raw in rules:
                if not isinstance(raw, dict):
                    continue
                tool_name = str(raw.get("tool_name", "")).strip()
                verdict = str(raw.get("verdict", "")).strip().lower()
                if tool_name and verdict:
                    normalized.append(
                        {
                            "tool_name": tool_name,
                            "verdict": verdict,
                            "reason": str(raw.get("reason", "")).strip(),
                        }
                    )
        return normalized


PermissionPolicy = PermissionControlPlane


__all__ = [
    "PermissionControlPlane",
    "PermissionMode",
    "PermissionPolicy",
    "PermissionRule",
    "PolicySource",
    "RuleVerdict",
]
