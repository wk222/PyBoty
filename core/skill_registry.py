"""Registry for discovered skills, prompt extensions, and executable skill tools."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from langchain.tools import BaseTool

from .skill_models import SkillDefinition
from .skill_prompts import render_active_skill_extensions
from .skill_sources import SkillSource
from .skill_storage import SkillStorage
from .skill_tool_resolver import SkillToolCache, load_skill_tools


class SkillRegistry:
    """Manage discovered skills and expose them as prompts plus tools."""

    def __init__(
        self,
        skills_dir: str | None = "workspace/skills",
        *,
        skill_sources: list[str | Path | SkillSource] | None = None,
    ):
        self.storage = SkillStorage(skills_dir, sources=skill_sources)
        self.skills_dir = self.storage.effective_skills_dir
        self.skills: dict[str, SkillDefinition] = {}
        self._tool_cache = SkillToolCache()
        self.reload()

    def reload(self, *, refresh_sources: bool = False) -> None:
        self.skills = self.storage.discover(refresh_sources=refresh_sources)
        self._tool_cache.invalidate()

    async def areload(self, *, refresh_sources: bool = False) -> None:
        self.skills = await self.storage.adiscover(refresh_sources=refresh_sources)
        self._tool_cache.invalidate()

    def refresh_sources(self) -> list[dict[str, object]]:
        self.reload(refresh_sources=True)
        return self.list_sources()

    async def arefresh_sources(self) -> list[dict[str, object]]:
        await self.areload(refresh_sources=True)
        return await self.alist_sources()

    def refresh_source(self, source_name: str) -> dict[str, object]:
        self.storage.refresh_source(source_name)
        self.reload()
        return self.storage.get_source(source_name)

    async def arefresh_source(self, source_name: str) -> dict[str, object]:
        await self.storage.arefresh_source(source_name)
        await self.areload()
        return await self.storage.aget_source(source_name)

    def list_sources(self) -> list[dict[str, object]]:
        return self.storage.list_sources()

    async def alist_sources(self) -> list[dict[str, object]]:
        return await self.storage.alist_sources()

    def get_source(self, source_name: str) -> dict[str, object]:
        return self.storage.get_source(source_name)

    async def aget_source(self, source_name: str) -> dict[str, object]:
        return await self.storage.aget_source(source_name)

    def list_skills(self) -> dict[str, dict[str, Any]]:
        return {name: skill.to_dict() for name, skill in self.skills.items()}

    def get_skill(self, name: str) -> SkillDefinition | None:
        return self.skills.get(name)

    def toggle_skill(self, name: str, enabled: bool) -> bool:
        skill = self.skills.get(name)
        if skill is None or not skill.writable:
            return False
        skill.enabled = enabled
        self.storage.set_enabled(skill, enabled)
        self._tool_cache.invalidate(name)
        return True

    async def atoggle_skill(self, name: str, enabled: bool) -> bool:
        skill = self.skills.get(name)
        if skill is None or not skill.writable:
            return False
        skill.enabled = enabled
        await self.storage.aset_enabled(skill, enabled)
        self._tool_cache.invalidate(name)
        return True

    def get_active_prompt_extensions(self, progressive: bool = True) -> str:
        return render_active_skill_extensions(
            self.skills.values(),
            progressive=progressive,
            skills_dir=self.skills_dir,
        )

    def get_active_tool_definitions(self) -> list[dict[str, Any]]:
        definitions: list[dict[str, Any]] = []
        for skill in self.skills.values():
            if skill.enabled and skill.tools:
                definitions.extend(skill.tools)
        return definitions

    def get_active_tools(self) -> list[BaseTool]:
        return self._tool_cache.get_active_tools(self.skills, self._load_skill_tools)

    def reload_skill_tools(self, name: str) -> list[BaseTool]:
        self._tool_cache.invalidate(name)
        return self.get_active_tools()

    def install_skill(self, name: str, skill_def: SkillDefinition) -> bool:
        self.storage.install(name, skill_def)
        self.reload()
        return True

    def install_skill_bundle(self, name: str, files: dict[str, str]) -> bool:
        self.storage.install_bundle(name, files)
        self.reload()
        return True

    def import_skill_bundle(
        self,
        name: str,
        files: dict[str, str],
        *,
        target_source_name: str | None = None,
        overwrite: bool = False,
    ) -> dict[str, object]:
        result = self.storage.install_bundle(
            name,
            files,
            target_source_name=target_source_name,
            overwrite=overwrite,
        )
        self.reload()
        return result

    async def aimport_skill_bundle(
        self,
        name: str,
        files: dict[str, str],
        *,
        target_source_name: str | None = None,
        overwrite: bool = False,
    ) -> dict[str, object]:
        result = await self.storage.ainstall_bundle(
            name,
            files,
            target_source_name=target_source_name,
            overwrite=overwrite,
        )
        await self.areload()
        return result

    def remove_skill(self, name: str) -> bool:
        skill = self.skills.get(name)
        if skill is None or not skill.writable:
            return False
        self.storage.remove(skill)
        self.reload()
        return True

    def _load_skill_tools(self, skill_name: str, skill: SkillDefinition) -> list[BaseTool]:
        materialized_skill_dir = self.storage.materialize_skill(skill)
        return load_skill_tools(
            skill_name=skill_name,
            skill=skill,
            materialized_skill_dir=materialized_skill_dir,
        )

    def skill_dir(self, name: str) -> Path | None:
        skill = self.skills.get(name)
        if skill is None:
            return None
        return self.storage.skill_dir(name, skill=skill)

    def list_skill_files(self, name: str) -> list[dict[str, int | str]] | None:
        skill = self.skills.get(name)
        if skill is None:
            return None
        return self.storage.list_files(skill)

    async def alist_skill_files(self, name: str) -> list[dict[str, int | str]] | None:
        skill = self.skills.get(name)
        if skill is None:
            return None
        return await self.storage.alist_files(skill)

    def read_skill_file(self, name: str, relative_path: str) -> str | None:
        skill = self.skills.get(name)
        if skill is None:
            return None
        return self.storage.read_file(skill, relative_path)

    async def aread_skill_file(self, name: str, relative_path: str) -> str | None:
        skill = self.skills.get(name)
        if skill is None:
            return None
        return await self.storage.aread_file(skill, relative_path)

    def write_skill_file(self, name: str, relative_path: str, content: str) -> bool:
        skill = self.skills.get(name)
        if skill is None or not skill.writable:
            return False
        self.storage.write_file(skill, relative_path, content)
        self.reload()
        return True

    async def awrite_skill_file(self, name: str, relative_path: str, content: str) -> bool:
        skill = self.skills.get(name)
        if skill is None or not skill.writable:
            return False
        await self.storage.awrite_file(skill, relative_path, content)
        await self.areload()
        return True

    def export_skill_bundle(self, name: str) -> dict[str, str] | None:
        skill = self.skills.get(name)
        if skill is None:
            return None
        return self.storage.export_bundle(skill)

    async def aexport_skill_bundle(self, name: str) -> dict[str, str] | None:
        skill = self.skills.get(name)
        if skill is None:
            return None
        return await self.storage.aexport_bundle(skill)

    def copy_skill_to_source(
        self,
        name: str,
        *,
        target_source_name: str,
        target_name: str | None = None,
        overwrite: bool = False,
    ) -> dict[str, object] | None:
        skill = self.skills.get(name)
        if skill is None:
            return None
        result = self.storage.copy_skill(
            skill,
            target_source_name=target_source_name,
            target_name=target_name,
            overwrite=overwrite,
        )
        self.reload()
        return result

    async def acopy_skill_to_source(
        self,
        name: str,
        *,
        target_source_name: str,
        target_name: str | None = None,
        overwrite: bool = False,
    ) -> dict[str, object] | None:
        skill = self.skills.get(name)
        if skill is None:
            return None
        result = await self.storage.acopy_skill(
            skill,
            target_source_name=target_source_name,
            target_name=target_name,
            overwrite=overwrite,
        )
        await self.areload()
        return result
