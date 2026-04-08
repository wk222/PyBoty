"""Agent-callable tools for permission control-plane management."""

from __future__ import annotations

import json
from typing import Any

from langchain.tools import BaseTool
from pydantic import BaseModel, Field


class SetPermissionModeInput(BaseModel):
    mode: str = Field(description="目标权限模式: default / plan / bypass")


class SetPermissionRuleInput(BaseModel):
    tool_name: str = Field(description="工具名，例如 read_file / bash")
    verdict: str = Field(description="规则裁决: allow / deny / ask")
    reason: str = Field(default="", description="规则原因，便于恢复或审计")
    source: str = Field(default="session", description="规则来源，例如 session / user / policy")


class RemovePermissionRuleInput(BaseModel):
    tool_name: str = Field(description="要移除规则的工具名")


class _PermissionToolBase(BaseTool):
    middleware: Any = None
    risk_level: str = "high"

    def _snapshot(self) -> dict[str, Any]:
        if self.middleware is None or not hasattr(self.middleware, "get_permission_snapshot"):
            return {"success": False, "error": "Permission middleware not configured"}
        snapshot = self.middleware.get_permission_snapshot()
        if not isinstance(snapshot, dict):
            return {"success": False, "error": "Permission middleware returned invalid snapshot"}
        return {"success": True, "permission": snapshot}


class GetPermissionStateTool(_PermissionToolBase):
    name: str = "get_permission_state"
    description: str = (
        "Inspect the current permission control plane, including active mode, "
        "session rules, and recent governance changes."
    )
    risk_level: str = "low"

    def _run(self) -> str:
        return json.dumps(self._snapshot(), ensure_ascii=False)


class SetPermissionModeTool(_PermissionToolBase):
    name: str = "set_permission_mode"
    description: str = (
        "Change the active permission mode for this session. "
        "Use plan for read-only planning, default for normal guarded work, "
        "and bypass only for trusted automation."
    )
    args_schema: type[BaseModel] = SetPermissionModeInput

    def _run(self, mode: str) -> str:
        if self.middleware is None or not hasattr(self.middleware, "set_permission_mode"):
            return json.dumps({"success": False, "error": "Permission middleware not configured"}, ensure_ascii=False)
        snapshot = self.middleware.set_permission_mode(mode)
        return json.dumps(
            {
                "success": True,
                "message": f"Permission mode set to {snapshot.get('mode', mode)}",
                "permission": snapshot,
            },
            ensure_ascii=False,
        )


class SetPermissionRuleTool(_PermissionToolBase):
    name: str = "set_permission_rule"
    description: str = (
        "Create or update a session-scoped permission rule for a specific tool. "
        "This is how the agent formalizes allow / deny / ask overrides."
    )
    args_schema: type[BaseModel] = SetPermissionRuleInput

    def _run(
        self,
        tool_name: str,
        verdict: str,
        reason: str = "",
        source: str = "session",
    ) -> str:
        if self.middleware is None or not hasattr(self.middleware, "add_permission_rule"):
            return json.dumps({"success": False, "error": "Permission middleware not configured"}, ensure_ascii=False)
        snapshot = self.middleware.add_permission_rule(
            tool_name=tool_name,
            verdict=verdict,
            reason=reason,
            source=source,
        )
        return json.dumps(
            {
                "success": True,
                "message": f"Permission rule set for {tool_name}: {verdict}",
                "permission": snapshot,
            },
            ensure_ascii=False,
        )


class RemovePermissionRuleTool(_PermissionToolBase):
    name: str = "remove_permission_rule"
    description: str = "Remove a session-scoped permission override for a specific tool."
    args_schema: type[BaseModel] = RemovePermissionRuleInput

    def _run(self, tool_name: str) -> str:
        if self.middleware is None or not hasattr(self.middleware, "remove_permission_rule"):
            return json.dumps({"success": False, "error": "Permission middleware not configured"}, ensure_ascii=False)
        snapshot = self.middleware.remove_permission_rule(tool_name)
        return json.dumps(
            {
                "success": True,
                "message": f"Permission rule removed for {tool_name}",
                "permission": snapshot,
            },
            ensure_ascii=False,
        )


class ClearPermissionRulesTool(_PermissionToolBase):
    name: str = "clear_permission_rules"
    description: str = "Clear all session-scoped permission overrides and revert to mode defaults."

    def _run(self) -> str:
        if self.middleware is None or not hasattr(self.middleware, "clear_permission_rules"):
            return json.dumps({"success": False, "error": "Permission middleware not configured"}, ensure_ascii=False)
        snapshot = self.middleware.clear_permission_rules()
        return json.dumps(
            {
                "success": True,
                "message": "Cleared all session permission rules",
                "permission": snapshot,
            },
            ensure_ascii=False,
        )


def get_permission_tools(middleware: Any) -> list[BaseTool]:
    return [
        GetPermissionStateTool(middleware=middleware),
        SetPermissionModeTool(middleware=middleware),
        SetPermissionRuleTool(middleware=middleware),
        RemovePermissionRuleTool(middleware=middleware),
        ClearPermissionRulesTool(middleware=middleware),
    ]
