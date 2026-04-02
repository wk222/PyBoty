"""Storage helpers for skill discovery and persistence."""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

from .skill_backend_runtime import SkillBackendRuntime
from .skill_loading import (
    adiscover_skills,
    build_skill_bundle,
    discover_skills,
    persist_skill_state,
)
from .skill_models import SkillDefinition
from .skill_sources import SkillSource, normalize_skill_sources, pick_writable_skill_source


class SkillStorage:
    """Persist skill definitions and enabled state on disk."""

    def __init__(
        self,
        skills_dir: str | Path | None = None,
        *,
        sources: Sequence[str | Path | SkillSource] | None = None,
    ):
        self.sources = normalize_skill_sources(skills_dir, sources)
        self._source_by_name = {source.name: source for source in self.sources}
        self.writable_source = pick_writable_skill_source(self.sources)
        self.backend_runtime = SkillBackendRuntime()
        if self.writable_source is not None:
            writable_local_root = self.writable_source.backend.local_path(str(self.writable_source.path))
            self.skills_dir = writable_local_root.resolve() if writable_local_root is not None else None
            self.writable_source.backend.ensure_root(str(self.writable_source.path))
        else:
            self.skills_dir = None

    def discover(self, *, refresh_sources: bool = False) -> dict[str, SkillDefinition]:
        return discover_skills(self.sources, refresh_sources=refresh_sources)

    async def adiscover(self, *, refresh_sources: bool = False) -> dict[str, SkillDefinition]:
        return await adiscover_skills(self.sources, refresh_sources=refresh_sources)

    def list_sources(self) -> list[dict[str, object]]:
        return [source.to_dict() for source in self.sources]

    async def alist_sources(self) -> list[dict[str, object]]:
        return [await source.to_dict_async() for source in self.sources]

    def set_enabled(self, skill: SkillDefinition, enabled: bool) -> None:
        source = self._source_for(skill)
        manifest_path = source.backend.join(skill.skill_path, "SKILL.md")
        updated = persist_skill_state(source.backend.read_text(manifest_path), enabled)
        source.backend.write_text(manifest_path, updated)

    async def aset_enabled(self, skill: SkillDefinition, enabled: bool) -> None:
        source = self._source_for(skill)
        manifest_path = source.backend.join(skill.skill_path, "SKILL.md")
        updated = persist_skill_state(await source.backend.aread_text(manifest_path), enabled)
        await source.backend.awrite_text(manifest_path, updated)

    def install(self, name: str, skill_def: SkillDefinition) -> None:
        self.install_bundle(name, build_skill_bundle(skill_def), overwrite=True)

    def install_bundle(
        self,
        name: str,
        files: dict[str, str],
        *,
        target_source_name: str | None = None,
        overwrite: bool = True,
    ) -> dict[str, object]:
        target_source = self._target_source(target_source_name)
        skill_root = target_source.backend.join(str(target_source.path), name)
        if target_source.backend.exists(skill_root) and not overwrite:
            raise FileExistsError(name)
        target_source.backend.write_bundle(skill_root, files, replace=overwrite)
        return {
            "skill_name": name,
            "source_name": target_source.name,
            "backend": target_source.backend.backend_name,
            "skill_path": skill_root,
            "overwrite": overwrite,
        }

    async def ainstall_bundle(
        self,
        name: str,
        files: dict[str, str],
        *,
        target_source_name: str | None = None,
        overwrite: bool = True,
    ) -> dict[str, object]:
        target_source = self._target_source(target_source_name)
        skill_root = target_source.backend.join(str(target_source.path), name)
        if await target_source.backend.aexists(skill_root) and not overwrite:
            raise FileExistsError(name)
        await target_source.backend.awrite_bundle(skill_root, files, replace=overwrite)
        return {
            "skill_name": name,
            "source_name": target_source.name,
            "backend": target_source.backend.backend_name,
            "skill_path": skill_root,
            "overwrite": overwrite,
        }

    def remove(self, skill: SkillDefinition) -> None:
        source = self._source_for(skill)
        source.backend.remove_tree(skill.skill_path)

    def skill_dir(self, name: str, *, skill: SkillDefinition | None = None) -> Path | None:
        if skill is not None and skill.skill_dir:
            return Path(skill.skill_dir)
        if skill is None and self.skills_dir is not None:
            return self.skills_dir / name
        return None

    def list_files(self, skill: SkillDefinition) -> list[dict[str, int | str]]:
        source = self._source_for(skill)
        return [{"path": entry.path, "size": entry.size} for entry in source.backend.list_files(skill.skill_path)]

    async def alist_files(self, skill: SkillDefinition) -> list[dict[str, int | str]]:
        source = self._source_for(skill)
        entries = await source.backend.alist_files(skill.skill_path)
        return [{"path": entry.path, "size": entry.size} for entry in entries]

    def read_file(self, skill: SkillDefinition, relative_path: str) -> str:
        source = self._source_for(skill)
        return source.backend.read_text(source.backend.join(skill.skill_path, relative_path))

    async def aread_file(self, skill: SkillDefinition, relative_path: str) -> str:
        source = self._source_for(skill)
        return await source.backend.aread_text(source.backend.join(skill.skill_path, relative_path))

    def write_file(self, skill: SkillDefinition, relative_path: str, content: str) -> None:
        source = self._source_for(skill)
        target = source.backend.join(skill.skill_path, relative_path)
        if not source.backend.exists(target):
            raise FileNotFoundError(relative_path)
        source.backend.write_text(target, content)

    async def awrite_file(self, skill: SkillDefinition, relative_path: str, content: str) -> None:
        source = self._source_for(skill)
        target = source.backend.join(skill.skill_path, relative_path)
        if not await source.backend.aexists(target):
            raise FileNotFoundError(relative_path)
        await source.backend.awrite_text(target, content)

    def export_bundle(self, skill: SkillDefinition) -> dict[str, str]:
        source = self._source_for(skill)
        return source.backend.read_bundle(skill.skill_path)

    async def aexport_bundle(self, skill: SkillDefinition) -> dict[str, str]:
        source = self._source_for(skill)
        return await source.backend.aread_bundle(skill.skill_path)

    def copy_skill(
        self,
        skill: SkillDefinition,
        *,
        target_source_name: str,
        target_name: str | None = None,
        overwrite: bool = False,
    ) -> dict[str, object]:
        bundle = self.export_bundle(skill)
        copied_name = target_name or skill.name
        result = self.install_bundle(
            copied_name,
            bundle,
            target_source_name=target_source_name,
            overwrite=overwrite,
        )
        result["copied_from"] = {
            "source_name": skill.source_name,
            "backend": skill.source_backend,
            "skill_path": skill.skill_path,
        }
        return result

    async def acopy_skill(
        self,
        skill: SkillDefinition,
        *,
        target_source_name: str,
        target_name: str | None = None,
        overwrite: bool = False,
    ) -> dict[str, object]:
        bundle = await self.aexport_bundle(skill)
        copied_name = target_name or skill.name
        result = await self.ainstall_bundle(
            copied_name,
            bundle,
            target_source_name=target_source_name,
            overwrite=overwrite,
        )
        result["copied_from"] = {
            "source_name": skill.source_name,
            "backend": skill.source_backend,
            "skill_path": skill.skill_path,
        }
        return result

    def materialize_skill(self, skill: SkillDefinition) -> Path:
        source = self._source_for(skill)
        return self.backend_runtime.materialize_skill(source, skill)

    async def amaterialize_skill(self, skill: SkillDefinition) -> Path:
        source = self._source_for(skill)
        return await self.backend_runtime.amaterialize_skill(source, skill)

    def _source_for(self, skill: SkillDefinition) -> SkillSource:
        source = self._source_by_name.get(skill.source_name)
        if source is None:
            raise KeyError(f"Unknown skill source: {skill.source_name}")
        return source

    @property
    def effective_skills_dir(self) -> str:
        """Resolve the best-effort skills directory path."""
        if self.skills_dir is not None:
            return str(self.skills_dir)
        if self.writable_source is not None:
            return str(self.writable_source.path)
        return ""

    def get_source(self, source_name: str) -> dict[str, object]:
        source = self._source_by_name.get(source_name)
        if source is None:
            raise KeyError(source_name)
        return source.to_dict()

    async def aget_source(self, source_name: str) -> dict[str, object]:
        source = self._source_by_name.get(source_name)
        if source is None:
            raise KeyError(source_name)
        return await source.to_dict_async()

    def refresh_source(self, source_name: str) -> None:
        source = self._source_by_name.get(source_name)
        if source is None:
            raise KeyError(source_name)
        source.backend.refresh_root(str(source.path))

    async def arefresh_source(self, source_name: str) -> None:
        source = self._source_by_name.get(source_name)
        if source is None:
            raise KeyError(source_name)
        await source.backend.arefresh_root(str(source.path))

    def _target_source(self, name: str | None) -> SkillSource:
        if name is None:
            if self.writable_source is None:
                raise PermissionError("No writable skill source configured")
            return self.writable_source
        source = self._source_by_name.get(name)
        if source is None:
            raise KeyError(f"Unknown skill source: {name}")
        if not source.writable:
            raise PermissionError(f"Skill source is read-only: {name}")
        return source
