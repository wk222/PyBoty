"""Backend-aware discovery and serialization helpers for skills."""

from __future__ import annotations

import json
import re
from collections.abc import Sequence
from pathlib import Path

from .skill_models import SkillDefinition
from .skill_sources import SkillSource


def _parse_frontmatter(raw: str) -> dict[str, object]:
    """Parse YAML frontmatter, falling back to line-split for simple cases."""
    try:
        import yaml

        data = yaml.safe_load(raw)
        if isinstance(data, dict):
            return data
    except Exception:
        pass
    result: dict[str, object] = {}
    for line in raw.strip().split("\n"):
        if ":" not in line or line.startswith(" ") or line.startswith("\t"):
            continue
        key, value = line.split(":", 1)
        result[key.strip()] = value.strip()
    return result


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
    homepage = ""
    skill_format = "pybot"
    capabilities: list[str] = []
    tools: list[dict] = []
    system_prompt_extension = ""
    enabled = True
    user_invocable = False
    uv_dependencies: list[str] = []
    metadata: dict[str, object] = {}
    requires_bins: list[str] = []
    requires_config: list[str] = []
    primary_env = ""

    is_openclaw = False
    openclaw_meta: dict = {}
    frontmatter_match = re.match(r"^---\s*\n(.*?)\n---\s*\n", content, re.DOTALL)
    if frontmatter_match:
        frontmatter = frontmatter_match.group(1)
        fm_data = _parse_frontmatter(frontmatter)
        name = fm_data.get("name", name)
        if fm_data.get("description"):
            raw_desc = fm_data["description"]
            description = raw_desc.strip('"').strip("'") if isinstance(raw_desc, str) else str(raw_desc)
        version = fm_data.get("version", version)
        author = fm_data.get("author", author)
        homepage_value = fm_data.get("homepage")
        if homepage_value:
            homepage = str(homepage_value).strip()
        if "enabled" in fm_data:
            enabled = str(fm_data["enabled"]).lower() == "true"
        if "user-invocable" in fm_data:
            user_invocable = str(fm_data["user-invocable"]).lower() == "true"
        elif "user_invocable" in fm_data:
            user_invocable = str(fm_data["user_invocable"]).lower() == "true"
        if homepage:
            capabilities.append(f"homepage: {homepage}")
        meta = fm_data.get("metadata")
        if isinstance(meta, dict):
            metadata = meta
        if isinstance(meta, dict) and "openclaw" in meta:
            is_openclaw = True
            skill_format = "openclaw"
            openclaw_meta = meta["openclaw"] if isinstance(meta["openclaw"], dict) else {}
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

    if is_openclaw and not system_prompt_extension:
        system_prompt_extension = content.strip()
        requires = openclaw_meta.get("requires", {})
        if isinstance(requires, dict):
            bins = requires.get("bins", [])
            if isinstance(bins, list):
                for b in bins:
                    requires_bins.append(str(b))
                    capabilities.append(f"requires-bin: {b}")
            configs = requires.get("config", [])
            if isinstance(configs, list):
                for item in configs:
                    requires_config.append(str(item))
                    capabilities.append(f"requires-config: {item}")
        primary_env_value = openclaw_meta.get("primaryEnv")
        if isinstance(primary_env_value, str) and primary_env_value.strip():
            primary_env = primary_env_value.strip()
            capabilities.append(f"primary-env: {primary_env}")

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
    from .skill_models import validate_skill_descriptor
    
    try:
        validated_data = validate_skill_descriptor({
            "name": name,
            "description": description,
            "version": version,
            "author": author,
            "homepage": homepage,
            "skill_format": skill_format,
            "capabilities": capabilities,
            "tools": tools,
            "system_prompt_extension": system_prompt_extension,
            "enabled": enabled,
            "user_invocable": user_invocable,
            "uv_dependencies": uv_dependencies,
            "metadata": metadata,
            "openclaw_metadata": openclaw_meta,
            "requires_bins": list(dict.fromkeys(requires_bins)),
            "requires_config": list(dict.fromkeys(requires_config)),
            "primary_env": primary_env,
        })
    except Exception as e:
        print(f"⚠️ [SkillLoading] Validation failed for skill '{name}': {e}")
        return None

    return SkillDefinition(
        **validated_data,
        source_name=source.name,
        source_backend=source.backend.backend_name,
        source_path=str(source.path),
        skill_path=skill_path,
        skill_dir=str(local_dir) if local_dir is not None else "",
        writable=source.writable,
    )


def render_skill_file_content(skill: SkillDefinition, relative_path: str, content: str) -> str:
    """Render a skill file for runtime consumption.

    OpenClaw skills often rely on placeholders like ``{baseDir}`` in SKILL.md.
    Replace those placeholders when we know the materialized local skill path.
    """
    if relative_path != "SKILL.md" or skill.skill_format != "openclaw" or not skill.skill_dir:
        return content

    base_dir = Path(skill.skill_dir).as_posix()
    replacements = {
        "{baseDir}": base_dir,
        "{skillDir}": base_dir,
        "{base_dir}": base_dir,
    }
    rendered = content
    for placeholder, value in replacements.items():
        rendered = rendered.replace(placeholder, value)
    return rendered


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

    frontmatter_lines = [
        "---",
        f"name: {skill_def.name}",
        f"description: {skill_def.description}",
        f"version: {skill_def.version}",
        f"author: {skill_def.author}",
        f"enabled: {'true' if skill_def.enabled else 'false'}",
    ]
    if skill_def.homepage:
        frontmatter_lines.append(f"homepage: {skill_def.homepage}")
    if skill_def.user_invocable:
        frontmatter_lines.append("user-invocable: true")
    if skill_def.metadata:
        frontmatter_lines.append(f"metadata: {json.dumps(skill_def.metadata, ensure_ascii=False)}")
    frontmatter_lines.append("---")
    frontmatter = "\n".join(frontmatter_lines)

    return f"""{frontmatter}

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
