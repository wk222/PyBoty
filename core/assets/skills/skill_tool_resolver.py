"""Resolve executable tools from skill definitions."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from langchain.tools import BaseTool

from .skill_models import SkillDefinition
from .skill_runtime import build_tool_from_definition, load_python_module_tools, resolve_skill_runtime_env


class SkillToolCache:
    """Cache loaded tools per skill and deduplicate across skills."""

    def __init__(self) -> None:
        self._cache: dict[str, list[BaseTool]] = {}

    def invalidate(self, name: str | None = None) -> None:
        if name is None:
            self._cache.clear()
        else:
            self._cache.pop(name, None)

    def get_active_tools(
        self,
        skills: dict[str, SkillDefinition],
        load_fn: Callable[[str, SkillDefinition], list[BaseTool]],
    ) -> list[BaseTool]:
        all_tools: list[BaseTool] = []
        seen_names: set[str] = set()
        for skill_name, skill in skills.items():
            if not skill.enabled:
                continue
            if skill_name not in self._cache:
                self._cache[skill_name] = load_fn(skill_name, skill)
            for tool in self._cache[skill_name]:
                if tool.name in seen_names:
                    continue
                seen_names.add(tool.name)
                all_tools.append(tool)
        return all_tools


def load_skill_tools(
    *,
    skill_name: str,
    skill: SkillDefinition,
    materialized_skill_dir: Path | None = None,
) -> list[BaseTool]:
    """Load unique tools for a single skill from declarative and Python sources."""
    resolved_tools: list[BaseTool] = []
    seen_names: set[str] = set()
    env_overrides = resolve_skill_runtime_env(skill)

    for tool in _iter_definition_tools(skill_name=skill_name, skill=skill, env_overrides=env_overrides):
        if tool.name in seen_names:
            continue
        seen_names.add(tool.name)
        resolved_tools.append(tool)

    if materialized_skill_dir is not None:
        for tool in load_python_module_tools(materialized_skill_dir, skill_name, env_overrides=env_overrides):
            if tool.name in seen_names:
                continue
            seen_names.add(tool.name)
            resolved_tools.append(tool)

    return resolved_tools


def _iter_definition_tools(*, skill_name: str, skill: SkillDefinition, env_overrides: dict[str, str]):
    for tool_def in skill.tools:
        try:
            tool = build_tool_from_definition(tool_def, skill_name, env_overrides=env_overrides)
        except Exception as exc:
            print(f"[SkillRegistry] 构建工具 {tool_def.get('name', '?')} 失败: {exc}")
            continue
        if tool is not None:
            yield tool
