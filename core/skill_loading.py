"""Backend-aware discovery and serialization helpers for skills."""

from __future__ import annotations

import json
import re
from collections.abc import Sequence

from .skill_models import SkillDefinition
from .skill_sources import SkillSource


def discover_skills(
    skill_sources: Sequence[SkillSource],
    *,
    refresh_sources: bool = False,
) -> dict[str, SkillDefinition]:
    """Discover skills from ordered sources, letting later sources override earlier ones."""
    skills: dict[str, SkillDefinition] = {}

    for source in skill_sources:
        root = str(source.path)
        if refresh_sources:
            source.backend.refresh_root(root)
        if not source.backend.exists(root):
            continue

        for skill_name in source.backend.list_skill_dirs(root):
            skill_root = source.backend.join(root, skill_name)
            try:
                skill = parse_skill_bundle(
                    dir_name=skill_name,
                    files=source.backend.read_bundle(skill_root),
                    source=source,
                    skill_path=skill_root,
                )
            except Exception as exc:
                print(f"[SkillRegistry] 加载技能 {skill_name} 失败: {exc}")
                continue
            if skill is not None:
                skills[skill.name] = skill
    return skills


async def adiscover_skills(
    skill_sources: Sequence[SkillSource],
    *,
    refresh_sources: bool = False,
) -> dict[str, SkillDefinition]:
    """Async variant of skill discovery for network-aware backends."""
    skills: dict[str, SkillDefinition] = {}

    for source in skill_sources:
        root = str(source.path)
        if refresh_sources:
            await source.backend.arefresh_root(root)
        if not await source.backend.aexists(root):
            continue

        for skill_name in await source.backend.alist_skill_dirs(root):
            skill_root = source.backend.join(root, skill_name)
            try:
                skill = parse_skill_bundle(
                    dir_name=skill_name,
                    files=await source.backend.aread_bundle(skill_root),
                    source=source,
                    skill_path=skill_root,
                )
            except Exception as exc:
                print(f"[SkillRegistry] 加载技能 {skill_name} 失败: {exc}")
                continue
            if skill is not None:
                skills[skill.name] = skill

    return skills


def parse_skill_bundle(
    dir_name: str,
    files: dict[str, str],
    *,
    source: SkillSource,
    skill_path: str,
) -> SkillDefinition | None:
    """Parse a backend-provided skill bundle into a skill definition."""
    content = files.get("SKILL.md")
    if content is None:
        return None
    return parse_skill_markdown(
        dir_name=dir_name,
        content=content,
        source=source,
        skill_path=skill_path,
        tools_content=files.get("tools.json"),
    )


def parse_skill_markdown(
    dir_name: str,
    content: str,
    *,
    source: SkillSource,
    skill_path: str,
    tools_content: str | None = None,
) -> SkillDefinition | None:
    """Parse a SKILL.md payload plus optional tools.json into a skill definition."""
    name = dir_name
    description = ""
    version = "1.0.0"
    author = "system"
    capabilities: list[str] = []
    tools: list[dict] = []
    system_prompt_extension = ""
    enabled = True
    uv_dependencies: list[str] = []

    frontmatter_match = re.match(r"^---\s*\n(.*?)\n---\s*\n", content, re.DOTALL)
    if frontmatter_match:
        frontmatter = frontmatter_match.group(1)
        for line in frontmatter.strip().split("\n"):
            if ":" not in line:
                continue
            key, value = line.split(":", 1)
            key = key.strip()
            value = value.strip()
            if key == "name":
                name = value
            elif key == "description":
                description = value
            elif key == "version":
                version = value
            elif key == "author":
                author = value
            elif key == "enabled":
                enabled = value.lower() == "true"
        content = content[frontmatter_match.end() :]

    if not description:
        for line in content.split("\n"):
            stripped = line.strip()
            if stripped and not stripped.startswith("#"):
                description = stripped
                break

    cap_match = re.search(r"## (?:能力|Capabilities)\s*\n(.*?)(?=\n## |\Z)", content, re.DOTALL)
    if cap_match:
        for line in cap_match.group(1).strip().split("\n"):
            item = line.strip().lstrip("-").strip()
            if item:
                capabilities.append(item)

    prompt_match = re.search(
        r"## (?:系统提示|System Prompt|Prompt Extension)\s*\n(.*?)(?=\n## |\Z)",
        content,
        re.DOTALL,
    )
    if prompt_match:
        system_prompt_extension = prompt_match.group(1).strip()

    deps_match = re.search(r"## (?:依赖|Dependencies)\s*\n(.*?)(?=\n## |\Z)", content, re.DOTALL)
    if deps_match:
        for line in deps_match.group(1).strip().split("\n"):
            item = line.strip().lstrip("-").strip()
            if item:
                uv_dependencies.append(item)

    if tools_content:
        try:
            loaded = json.loads(tools_content)
            tools = loaded if isinstance(loaded, list) else [loaded]
        except Exception:
            tools = []

    local_dir = source.backend.local_path(skill_path)
    return SkillDefinition(
        name=name,
        description=description,
        version=version,
        author=author,
        capabilities=capabilities,
        tools=tools,
        system_prompt_extension=system_prompt_extension,
        enabled=enabled,
        uv_dependencies=uv_dependencies,
        source_name=source.name,
        source_backend=source.backend.backend_name,
        source_path=str(source.path),
        skill_path=skill_path,
        skill_dir=str(local_dir) if local_dir is not None else "",
        writable=source.writable,
    )


def persist_skill_state(content: str, enabled: bool) -> str:
    """Return SKILL.md content with the enabled flag updated."""
    if re.match(r"^---\s*\n", content):
        if re.search(r"(^|\n)enabled:\s*(true|false)\b", content):
            return re.sub(
                r"(enabled:\s*)(true|false)",
                f"\\g<1>{'true' if enabled else 'false'}",
                content,
                count=1,
            )
        return re.sub(
            r"^---\s*\n",
            f"---\nenabled: {'true' if enabled else 'false'}\n",
            content,
            count=1,
        )
    return f"---\nenabled: {'true' if enabled else 'false'}\n---\n" + content


def render_skill_markdown(skill_def: SkillDefinition) -> str:
    """Serialize a skill definition into SKILL.md content."""
    capabilities = "\n".join(f"- {capability}" for capability in skill_def.capabilities)
    deps_section = ""
    if skill_def.uv_dependencies:
        deps_lines = "\n".join(f"- {dep}" for dep in skill_def.uv_dependencies)
        deps_section = f"\n## 依赖\n{deps_lines}\n"

    return f"""---
name: {skill_def.name}
description: {skill_def.description}
version: {skill_def.version}
author: {skill_def.author}
enabled: {"true" if skill_def.enabled else "false"}
---

# {skill_def.name}

{skill_def.description}

## 能力
{capabilities}

## 系统提示
{skill_def.system_prompt_extension}
{deps_section}"""


def build_skill_bundle(skill_def: SkillDefinition) -> dict[str, str]:
    """Serialize a skill definition into backend-agnostic skill files."""
    bundle = {"SKILL.md": render_skill_markdown(skill_def)}
    if skill_def.tools:
        bundle["tools.json"] = serialize_tools_json(skill_def)
    return bundle


def serialize_tools_json(skill_def: SkillDefinition) -> str:
    """Serialize declarative tool definitions for backend persistence."""
    return json.dumps(skill_def.tools, ensure_ascii=False, indent=2)
