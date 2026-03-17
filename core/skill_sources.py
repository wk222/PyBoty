"""Named skill source helpers for layered skill discovery."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path

from .skill_backends import FilesystemSkillBackend, SkillBackend


@dataclass(frozen=True)
class SkillSource:
    """A named skill source directory with precedence and mutability metadata."""

    name: str
    path: str | Path
    writable: bool = False
    backend: SkillBackend = field(default_factory=FilesystemSkillBackend)

    def resolved(self) -> SkillSource:
        return SkillSource(
            name=self.name,
            path=self.backend.normalize_root(self.path),
            writable=self.writable,
            backend=self.backend,
        )

    def to_dict(self) -> dict[str, object]:
        root_info = self.backend.describe_root(str(self.path))
        capabilities = self.backend.capabilities()
        refresh_report = self.backend.get_refresh_report(str(self.path))
        descriptor = self.backend.get_source_descriptor(str(self.path))
        return {
            "name": self.name,
            "path": str(self.path),
            "writable": self.writable,
            "backend": self.backend.backend_name,
            "exists": root_info.exists,
            "display_path": root_info.display_path,
            "local_path": root_info.local_path,
            "remote": root_info.remote,
            "metadata": root_info.metadata,
            "capabilities": capabilities.to_dict(),
            "refresh_report": refresh_report,
            "descriptor": descriptor,
        }

    async def to_dict_async(self) -> dict[str, object]:
        root_info = await self.backend.adescribe_root(str(self.path))
        capabilities = self.backend.capabilities()
        refresh_report = await self.backend.aget_refresh_report(str(self.path))
        descriptor = await self.backend.aget_source_descriptor(str(self.path))
        return {
            "name": self.name,
            "path": str(self.path),
            "writable": self.writable,
            "backend": self.backend.backend_name,
            "exists": root_info.exists,
            "display_path": root_info.display_path,
            "local_path": root_info.local_path,
            "remote": root_info.remote,
            "metadata": root_info.metadata,
            "capabilities": capabilities.to_dict(),
            "refresh_report": refresh_report,
            "descriptor": descriptor,
        }


def normalize_skill_sources(
    skills_dir: str | Path | None = None,
    skill_sources: Sequence[str | Path | SkillSource] | None = None,
) -> list[SkillSource]:
    """Normalize string/path skill directories into explicit named sources."""
    if skill_sources is None:
        default_path = Path(skills_dir or "workspace/skills")
        return [SkillSource(name=_source_name(default_path, 0), path=default_path, writable=True).resolved()]

    normalized: list[SkillSource] = []
    all_explicit = True
    for index, source in enumerate(skill_sources):
        if isinstance(source, SkillSource):
            normalized.append(source.resolved())
            continue
        all_explicit = False
        normalized.append(SkillSource(name=_source_name(source, index), path=source, writable=False).resolved())

    if not any(source.writable for source in normalized) and not all_explicit:
        last = normalized[-1]
        normalized[-1] = SkillSource(name=last.name, path=last.path, writable=True, backend=last.backend)
    return normalized


def pick_writable_skill_source(sources: Sequence[SkillSource]) -> SkillSource | None:
    """Return the highest-precedence writable source, if any."""
    for source in reversed(sources):
        if source.writable:
            return source
    return None


def _source_name(path: str | Path, index: int) -> str:
    name = Path(str(path)).name.strip()
    return name or f"skills_{index}"
