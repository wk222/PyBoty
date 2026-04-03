"""Unified discovery and build layer across ToolStorage and SkillRegistry.

Skills surface here as groups of ``UnifiedToolInfo`` entries with
``layer="skill_tool"``.  The caller never needs to know whether a tool
came from a raw ToolStorage JSON file or from a bundled Skill.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from langchain.tools import BaseTool

from .tool_creator import create_dynamic_tool
from .tool_storage import ToolStorage
from .unified_tool_info import LAYER_SKILL_TOOL, LAYER_TOOL, UnifiedToolInfo

if TYPE_CHECKING:
    from core.assets.skills.skill_registry import SkillRegistry


class UnifiedAssetInventory:
    """Single query surface for all agent-executable capabilities.

    Parameters
    ----------
    tool_storage:
        Global ToolStorage instance.  May be ``None`` for skill-only usage.
    skill_registry:
        SkillRegistry instance.  May be ``None`` if skills are disabled.

    Usage
    -----
    >>> inv = UnifiedAssetInventory(tool_storage=ts, skill_registry=sr)
    >>> all_info = inv.list_all()
    >>> tools = inv.build_langchain_tools()
    """

    def __init__(
        self,
        tool_storage: ToolStorage | None = None,
        skill_registry: "SkillRegistry | None" = None,
    ) -> None:
        self._tool_storage = tool_storage
        self._skill_registry = skill_registry

    # ------------------------------------------------------------------
    # Discovery
    # ------------------------------------------------------------------

    def list_all(self) -> list[UnifiedToolInfo]:
        """Return all known tools, merging direct tools and skill tools.

        Direct tools win on name collision (same name registered in both
        ToolStorage and a Skill bundle).
        """
        result: list[UnifiedToolInfo] = []
        seen: set[str] = set()

        for info in self._iter_direct_tools():
            seen.add(info.name)
            result.append(info)

        for info in self._iter_skill_tools():
            if info.name in seen:
                continue
            seen.add(info.name)
            result.append(info)

        return result

    def get(self, name: str) -> UnifiedToolInfo | None:
        """Look up a single tool by name across all sources."""
        if self._tool_storage:
            tool_def = self._tool_storage.get_tool(name)
            if tool_def is not None:
                return UnifiedToolInfo.from_tool_def(name, tool_def)

        if self._skill_registry:
            for skill_name, skill in self._skill_registry.skills.items():
                if not skill.enabled:
                    continue
                for tool_def in skill.tools:
                    if str(tool_def.get("name", "")) == name:
                        return UnifiedToolInfo.from_skill_tool_def(
                            tool_def,
                            skill_name=skill_name,
                            skill_enabled=skill.enabled,
                            skill_tags=skill.capabilities,
                            system_prompt_extension=skill.system_prompt_extension,
                        )

        return None

    def find(
        self,
        *,
        query: str = "",
        layer: str = "",
        tags: list[str] | None = None,
    ) -> list[UnifiedToolInfo]:
        """Filtered search across all tools.

        Parameters
        ----------
        query:
            Case-insensitive substring match against name + description.
        layer:
            ``"tool"`` for direct tools only, ``"skill_tool"`` for skill-backed
            tools only.  Empty string returns both.
        tags:
            If provided, only tools whose ``tags`` list contains ALL of the
            requested tags are returned.
        """
        results: list[UnifiedToolInfo] = []
        query_lower = query.lower()
        required_tags = set(tags) if tags else set()

        for info in self.list_all():
            if layer and info.layer != layer:
                continue
            if query_lower and query_lower not in info.name.lower() and query_lower not in info.description.lower():
                continue
            if required_tags and not required_tags.issubset(set(info.tags)):
                continue
            results.append(info)

        return results

    def enabled_names(self) -> list[str]:
        """Names of all currently enabled tools."""
        return [info.name for info in self.list_all() if info.enabled]

    def list_by_source(self, source: str) -> list[UnifiedToolInfo]:
        """All tools belonging to a given source (e.g. ``"skill:web_search"``)."""
        return [info for info in self.list_all() if info.source == source]

    # ------------------------------------------------------------------
    # Building LangChain tools
    # ------------------------------------------------------------------

    def build_langchain_tools(
        self,
        names: list[str] | None = None,
        *,
        include_disabled: bool = False,
    ) -> list[BaseTool]:
        """Return ready-to-use LangChain BaseTool instances.

        Parameters
        ----------
        names:
            Subset of tool names to build.  ``None`` builds all enabled tools.
        include_disabled:
            When ``True``, disabled skill tools are also built.
        """
        if names is not None:
            return self._build_named(names, include_disabled=include_disabled)
        return self._build_all(include_disabled=include_disabled)

    def build_tool(self, name: str) -> BaseTool | None:
        """Build a single tool by name.  Returns ``None`` if not found."""
        result = self._build_named([name], include_disabled=True)
        return result[0] if result else None

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _build_all(self, *, include_disabled: bool) -> list[BaseTool]:
        all_tools: list[BaseTool] = []
        seen: set[str] = set()

        if self._tool_storage:
            for tool_def in self._tool_storage.tools.values():
                try:
                    tool = create_dynamic_tool(tool_def)
                    if tool.name not in seen:
                        seen.add(tool.name)
                        all_tools.append(tool)
                except Exception:
                    pass

        if self._skill_registry:
            for tool in self._skill_registry.get_active_tools():
                if tool.name not in seen:
                    seen.add(tool.name)
                    all_tools.append(tool)

        return all_tools

    def _build_named(self, names: list[str], *, include_disabled: bool) -> list[BaseTool]:
        name_set = set(names)
        all_tools: list[BaseTool] = []
        found: set[str] = set()

        if self._tool_storage:
            for name in list(name_set - found):
                tool_def = self._tool_storage.get_tool(name)
                if tool_def is not None:
                    try:
                        all_tools.append(create_dynamic_tool(tool_def))
                        found.add(name)
                    except Exception:
                        pass

        remaining = name_set - found
        if remaining and self._skill_registry:
            for skill_tool in self._skill_registry.get_active_tools():
                if skill_tool.name in remaining:
                    all_tools.append(skill_tool)
                    found.add(skill_tool.name)
                    if not remaining - found:
                        break

        return all_tools

    def _iter_direct_tools(self):
        if not self._tool_storage:
            return
        for name, tool_def in self._tool_storage.tools.items():
            yield UnifiedToolInfo.from_tool_def(name, tool_def)

    def _iter_skill_tools(self):
        if not self._skill_registry:
            return
        for skill_name, skill in self._skill_registry.skills.items():
            for tool_def in skill.tools:
                tname = str(tool_def.get("name", ""))
                if not tname:
                    continue
                yield UnifiedToolInfo.from_skill_tool_def(
                    tool_def,
                    skill_name=skill_name,
                    skill_enabled=skill.enabled,
                    skill_tags=skill.capabilities,
                    system_prompt_extension=skill.system_prompt_extension,
                )

    # ------------------------------------------------------------------
    # Convenience summary
    # ------------------------------------------------------------------

    def summary(self) -> dict[str, Any]:
        """High-level inventory counts for logging and diagnostics."""
        all_info = self.list_all()
        direct = [i for i in all_info if i.layer == LAYER_TOOL]
        skill = [i for i in all_info if i.layer == LAYER_SKILL_TOOL]
        skill_groups: set[str] = {i.skill_name for i in skill if i.skill_name}
        return {
            "total": len(all_info),
            "direct_tools": len(direct),
            "skill_tools": len(skill),
            "skill_groups": sorted(skill_groups),
            "enabled": sum(1 for i in all_info if i.enabled),
            "disabled": sum(1 for i in all_info if not i.enabled),
        }
