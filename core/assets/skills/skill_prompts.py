"""Prompt rendering helpers for active skills.

Implements DeepAgents-style progressive disclosure: the system prompt
lists skill names/descriptions so the agent knows what is available,
but full SKILL.md instructions are only read on demand via ``read_file``.
"""

from __future__ import annotations

from collections.abc import Iterable

from .skill_models import SkillDefinition

SKILLS_SYSTEM_PROMPT = """
## Skills System

You have access to a skills library that provides specialized capabilities and domain knowledge.

{skills_locations}

**Available Skills:**

{skills_list}

**How to Use Skills (Progressive Disclosure):**

Skills follow a **progressive disclosure** pattern — you see their name and description above,
but only read full instructions when needed:

1. **Recognize when a skill applies**: check if the user's task matches a skill's description
2. **Read the skill's full instructions**: use ``read_file`` on the SKILL.md path shown above
3. **Follow the skill's instructions**: SKILL.md contains step-by-step workflows and examples
4. **Access supporting files**: skills may include helper scripts — use absolute paths
5. **For OpenClaw-style skills**: if a summary shows ``{{baseDir}}=...``,
   substitute that into any placeholders you read in SKILL.md

**When to Use Skills:**
- User's request matches a skill's domain
- You need specialized knowledge or structured workflows
- A skill provides proven patterns for complex tasks

Remember: Skills make you more capable and consistent. When in doubt, check if a skill exists!
"""


def render_active_skill_extensions(
    skills: Iterable[SkillDefinition],
    *,
    progressive: bool = True,
    skills_dir: str = "",
) -> str:
    """Render active skill descriptions or full prompt extensions."""
    active_skills = [skill for skill in skills if skill.enabled]
    if not active_skills:
        return ""

    if not progressive:
        sections: list[str] = []
        for skill in active_skills:
            if skill.system_prompt_extension:
                sections.append(f"### 技能: {skill.name}\n{skill.system_prompt_extension}")
        return "\n\n".join(sections)

    skills_list = "\n".join(_render_progressive_summary(skill) for skill in active_skills)
    locations = f"**Skills Directory**: `{skills_dir}`" if skills_dir else ""
    return SKILLS_SYSTEM_PROMPT.format(
        skills_locations=locations,
        skills_list=skills_list,
    )


def _render_progressive_summary(skill: SkillDefinition) -> str:
    capabilities = ", ".join(skill.capabilities[:5]) if skill.capabilities else "通用"
    tool_count = len(skill.tools)
    tools_info = f", {tool_count} tools" if tool_count else ""
    source_info = f" @{skill.source_name}" if skill.source_name else ""
    skill_path = skill.skill_dir or skill.skill_path
    path_info = f" — `{skill_path}/SKILL.md`" if skill_path else ""
    runtime_hints: list[str] = []
    if skill.skill_format == "openclaw":
        runtime_hints.append("format=openclaw")
        if skill.skill_dir:
            runtime_hints.append(f"{{baseDir}}={skill.skill_dir.replace(chr(92), '/')}")
        if skill.requires_bins:
            runtime_hints.append("bins=" + ",".join(skill.requires_bins[:3]))
        if skill.primary_env:
            runtime_hints.append(f"env={skill.primary_env}")
    hint_info = f" [{' | '.join(runtime_hints)}]" if runtime_hints else ""
    return (
        f"- **{skill.name}** (v{skill.version}): "
        f"{skill.description[:120]}{tools_info} [{capabilities}]{source_info}{path_info}{hint_info}"
    )
