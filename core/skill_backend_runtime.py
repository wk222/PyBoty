"""Runtime helpers that bridge skill sources to backend-agnostic local execution."""

from __future__ import annotations

import hashlib
import shutil
import tempfile
from pathlib import Path

from .skill_models import SkillDefinition
from .skill_sources import SkillSource


class SkillBackendRuntime:
    """Materialize backend-managed skills into local directories when execution needs them."""

    def __init__(self, materialization_root: str | Path | None = None):
        base_root = Path(materialization_root) if materialization_root is not None else Path(tempfile.gettempdir())
        self.materialization_root = (base_root / "pybot_skill_materializations").resolve()
        self.materialization_root.mkdir(parents=True, exist_ok=True)

    def local_skill_dir(self, source: SkillSource, skill: SkillDefinition) -> Path | None:
        if skill.skill_dir:
            return Path(skill.skill_dir)
        local_path = source.backend.local_path(skill.skill_path)
        return local_path.resolve() if local_path is not None else None

    def materialize_skill(self, source: SkillSource, skill: SkillDefinition) -> Path:
        local_dir = self.local_skill_dir(source, skill)
        if local_dir is not None:
            return local_dir

        destination = self._destination_for(source, skill)
        if destination.exists():
            shutil.rmtree(destination)
        destination.mkdir(parents=True, exist_ok=True)

        for relative_path, content in source.backend.read_bundle(skill.skill_path).items():
            target = destination / Path(relative_path)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")

        return destination

    async def amaterialize_skill(self, source: SkillSource, skill: SkillDefinition) -> Path:
        local_dir = self.local_skill_dir(source, skill)
        if local_dir is not None:
            return local_dir

        destination = self._destination_for(source, skill)
        if destination.exists():
            shutil.rmtree(destination)
        destination.mkdir(parents=True, exist_ok=True)

        for relative_path, content in (await source.backend.aread_bundle(skill.skill_path)).items():
            target = destination / Path(relative_path)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")

        return destination

    def _destination_for(self, source: SkillSource, skill: SkillDefinition) -> Path:
        skill_name = skill.name or Path(skill.skill_path).name or "skill"
        digest = hashlib.sha1(
            f"{source.backend.backend_name}:{source.name}:{skill.skill_path}".encode(),
            usedforsecurity=False,
        ).hexdigest()[:12]
        return self.materialization_root / source.name / f"{skill_name}-{digest}"
