"""Dynamic tool inventory and merge helpers for tool middleware."""

from __future__ import annotations

import json
from collections.abc import Sequence
from typing import TYPE_CHECKING, Any

from langchain.tools import BaseTool
from langchain_core.messages import ToolMessage

from .tool_creator import get_dynamic_tools
from .tool_storage import ToolStorage

if TYPE_CHECKING:
    from core.assets.skills.skill_registry import SkillRegistry


class DynamicToolInventory:
    """Track base tools, persisted dynamic tools, and mutation notices.

    When a ``skill_registry`` is provided, enabled skill tools are included
    alongside storage-backed dynamic tools so the agent sees a unified tool
    list without requiring a separate skill-injection call.
    """

    def __init__(
        self,
        tool_storage: ToolStorage | None = None,
        *,
        skill_registry: "SkillRegistry | None" = None,
    ):
        self._tool_storage = tool_storage
        self._skill_registry = skill_registry
        self._base_tools: list[BaseTool] = []
        self._current_tools: list[BaseTool] = []
        self._extra_dynamic_tool_names: set[str] = set()
        self._last_created_tool: str | None = None
        self._pending_mutation_notice = False

    @property
    def last_created_tool(self) -> str | None:
        return self._last_created_tool

    @property
    def tool_storage(self) -> ToolStorage | None:
        return self._tool_storage

    def list_dynamic_tools(self) -> list[BaseTool]:
        storage_tools = get_dynamic_tools(self._tool_storage) if self._tool_storage else []
        if not self._skill_registry:
            return storage_tools
        skill_tools = self._skill_registry.get_active_tools()
        return self.merge_tools(storage_tools, skill_tools)

    def set_base_tools(self, tools: Sequence[BaseTool]) -> None:
        self._base_tools = list(tools)
        self._current_tools = self.get_all_tools()

    def set_known_dynamic_tools(self, tool_names: Sequence[str]) -> None:
        self._extra_dynamic_tool_names = {str(name).strip() for name in tool_names if str(name).strip()}

    def get_all_tools(self) -> list[BaseTool]:
        merged = self.merge_tools(self._base_tools, self.list_dynamic_tools())
        return merged or list(self._current_tools)

    def refresh(self, dynamic_tools: Sequence[BaseTool] | None = None) -> list[BaseTool]:
        resolved_dynamic_tools = list(dynamic_tools) if dynamic_tools is not None else self.list_dynamic_tools()
        self._current_tools = self.merge_tools(self._base_tools, resolved_dynamic_tools)
        return list(self._current_tools)

    def fallback_to_base_tools(self) -> list[BaseTool]:
        self._current_tools = list(self._base_tools)
        return list(self._current_tools)

    def inject_tools(self, request: Any) -> tuple[Any, int]:
        existing_tools = list(request.tools or [])
        merged_tools = self.merge_tools(existing_tools, self._base_tools, self.list_dynamic_tools())
        added_count = max(len(merged_tools) - len(existing_tools), 0)
        if added_count:
            request = request.override(tools=merged_tools)
        self._current_tools = merged_tools
        return request, added_count

    def get_dynamic_tool_names(self) -> set[str]:
        dynamic_names = set(self._extra_dynamic_tool_names)
        if self._tool_storage:
            dynamic_names.update(self._tool_storage.list_tools().keys())
        if self._skill_registry:
            for tool in self._skill_registry.get_active_tools():
                dynamic_names.add(tool.name)
        return dynamic_names

    def is_dynamic_tool(self, tool_name: str) -> bool:
        return tool_name in self.get_dynamic_tool_names()

    def is_return_direct(self, tool_name: str) -> bool:
        for tool in self._current_tools:
            if tool.name == tool_name:
                return getattr(tool, "return_direct", False)
        return False

    def note_tool_mutation(self, *, tool_name: str, result: ToolMessage) -> None:
        if tool_name not in {"create_custom_tool", "remove_custom_tool"} or result.status == "error":
            return

        result_data = self._parse_json_payload(result.content)
        if not isinstance(result_data, dict) or not result_data.get("success"):
            return

        self._pending_mutation_notice = True
        if tool_name == "create_custom_tool":
            tool_name_value = str(result_data.get("tool_name", "")).strip()
            self._last_created_tool = tool_name_value or None
            return

        self._last_created_tool = None

    def pop_mutation_notice(self) -> str | None:
        if not self._pending_mutation_notice:
            return None
        self._pending_mutation_notice = False
        return self._last_created_tool

    def increment_usage(self, tool_name: str) -> None:
        if not self._tool_storage:
            return
        tool_def = self._tool_storage.get_tool(tool_name)
        if tool_def:
            tool_def["usage_count"] = tool_def.get("usage_count", 0) + 1

    @staticmethod
    def merge_tools(*tool_groups: Sequence[BaseTool]) -> list[BaseTool]:
        merged: list[BaseTool] = []
        seen: set[str] = set()
        for group in tool_groups:
            for tool in group:
                if tool.name in seen:
                    continue
                seen.add(tool.name)
                merged.append(tool)
        return merged

    @staticmethod
    def _parse_json_payload(content: Any) -> dict[str, Any] | None:
        if not isinstance(content, str):
            return None
        try:
            payload = json.loads(content)
        except Exception:
            return None
        return payload if isinstance(payload, dict) else None
