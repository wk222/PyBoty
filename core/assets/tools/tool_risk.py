"""Tool risk classification and enforcement.

Every tool is assigned a **RiskLevel** (low / medium / high / critical).
High-risk tools require human approval before execution; critical tools
are blocked by default.

Usage::

    from core.assets.tools.tool_risk import ToolRiskRegistry, RiskLevel

    registry = ToolRiskRegistry()
    registry.set_risk("exec_shell", RiskLevel.HIGH)
    registry.set_risk("search_web", RiskLevel.LOW)

    level = registry.get_risk("exec_shell")
    if level.requires_approval:
        # route to approval queue
        ...
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from enum import Enum
from typing import Any

logger = logging.getLogger(__name__)


class RiskLevel(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"

    @property
    def requires_approval(self) -> bool:
        return self in (RiskLevel.HIGH, RiskLevel.CRITICAL)

    @property
    def is_blocked(self) -> bool:
        return self == RiskLevel.CRITICAL


DEFAULT_DANGEROUS_TOOLS: set[str] = {
    "exec_shell",
    "execute_command",
    "run_command",
    "shell",
    "bash",
    "subprocess_run",
    "os_system",
    "eval_code",
    "exec_code",
    "file_delete",
    "rmdir",
    "drop_table",
    "delete_database",
}

DEFAULT_HIGH_RISK_TOOLS: set[str] = {
    "file_write",
    "file_overwrite",
    "send_email",
    "send_message",
    "http_request",
    "deploy",
    "publish",
    "create_function",
    "update_function",
}


@dataclass
class ToolRiskEntry:
    """Risk metadata for a single tool."""
    tool_name: str
    level: RiskLevel
    reason: str = ""
    requires_note: bool = False


class ToolRiskRegistry:
    """Global registry of tool risk classifications."""

    def __init__(self) -> None:
        self._registry: dict[str, ToolRiskEntry] = {}
        self._initialise_defaults()

    def _initialise_defaults(self) -> None:
        for name in DEFAULT_DANGEROUS_TOOLS:
            self._registry[name] = ToolRiskEntry(
                tool_name=name,
                level=RiskLevel.CRITICAL,
                reason="System-level operation — blocked by default",
            )
        for name in DEFAULT_HIGH_RISK_TOOLS:
            self._registry[name] = ToolRiskEntry(
                tool_name=name,
                level=RiskLevel.HIGH,
                reason="Side-effect operation — requires approval",
            )

    def set_risk(self, tool_name: str, level: RiskLevel, reason: str = "") -> None:
        self._registry[tool_name] = ToolRiskEntry(
            tool_name=tool_name,
            level=level,
            reason=reason,
        )

    def get_risk(self, tool_name: str) -> ToolRiskEntry:
        return self._registry.get(
            tool_name,
            ToolRiskEntry(tool_name=tool_name, level=RiskLevel.LOW),
        )

    def check(self, tool_name: str) -> dict[str, Any]:
        """Check risk and return an action dict.

        Returns::
            {"allowed": True/False, "requires_approval": True/False,
             "level": "low"|..., "reason": "..."}
        """
        entry = self.get_risk(tool_name)
        return {
            "allowed": not entry.level.is_blocked,
            "requires_approval": entry.level.requires_approval,
            "level": entry.level.value,
            "reason": entry.reason,
        }

    def list_by_level(self, level: RiskLevel) -> list[str]:
        return [name for name, e in self._registry.items() if e.level == level]

    def all_entries(self) -> list[ToolRiskEntry]:
        return list(self._registry.values())

    def allow_tool(self, tool_name: str) -> None:
        """Downgrade a tool to LOW risk (whitelist it)."""
        if tool_name in self._registry:
            self._registry[tool_name].level = RiskLevel.LOW
            self._registry[tool_name].reason = "Explicitly whitelisted"
        else:
            self.set_risk(tool_name, RiskLevel.LOW, "Explicitly whitelisted")

    def block_tool(self, tool_name: str, reason: str = "Manually blocked") -> None:
        """Upgrade a tool to CRITICAL (block it)."""
        self.set_risk(tool_name, RiskLevel.CRITICAL, reason)


_global_registry: ToolRiskRegistry | None = None


def get_tool_risk_registry() -> ToolRiskRegistry:
    global _global_registry
    if _global_registry is None:
        _global_registry = ToolRiskRegistry()
    return _global_registry
