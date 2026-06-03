"""
工作空间管理器 (WorkspaceManager)

统一管理团队工作空间 Markdown 文件（借鉴 CowAgent ~/cow 与 OpenClaw workspace）：
- SOUL.md / AGENT.md: 智能体人格
- IDENTITY.md / USER.md: 用户/操作者身份
- TEAM.md: 小团队约定（项目、沟通、权限）
- RULES.md: 行为边界与安全规则
- MEMORY.md: 长期记忆（摘要注入 system prompt）
- SCHEDULE.md: 定时任务
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

# CowAgent/OpenClaw 文件名 → PyBot canonical 名
_FILE_ALIASES: dict[str, str] = {
    "AGENT.md": "SOUL.md",
    "USER.md": "IDENTITY.md",
}

_TEAM_TEMPLATES: dict[str, str] = {
    "SOUL.md": """# Agent Soul

You are PyBot, the team's capable assistant.

- Be concise and actionable for a small engineering team.
- Prefer tools over guessing; cite files and commands when relevant.
- Ask one clarifying question when requirements are ambiguous.
""",
    "IDENTITY.md": """# Operator Identity

Describe who you are and how you want the agent to address you.

- Name:
- Role:
- Timezone:
- Preferred language: zh-CN
""",
    "TEAM.md": """# Team Context

## Mission
Describe what this team/builds and current priorities.

## Conventions
- Git: feature branches, PR review before merge
- Docs: update README when behavior changes
- Secrets: never paste API keys into chat

## Active Projects
- (add projects here)
""",
    "RULES.md": """# Rules & Safety

- Do not exfiltrate secrets or `.env` contents.
- Confirm before destructive shell commands or mass file deletes.
- Use focused canvas for read-only investigation when unsure.
- Escalate to human approval for production changes.
""",
    "MEMORY.md": """# Long-term Memory

Distilled facts appear here after memory pipeline runs.
Use bullet lines starting with `-` for each fact.
""",
    "SCHEDULE.md": """# Scheduled Tasks

Define cron-style tasks here or via the Web Settings schedule tab.
""",
}


class WorkspaceManager:
    WORKSPACE_FILES = {
        "SOUL.md": "智能体人格（可用 AGENT.md 别名）",
        "IDENTITY.md": "操作者身份（可用 USER.md 别名）",
        "TEAM.md": "团队约定与项目上下文",
        "RULES.md": "行为边界与安全规则",
        "MEMORY.md": "长期记忆",
        "SCHEDULE.md": "定时任务配置",
    }

    def __init__(self, workspace_dir: str = "workspace"):
        self.workspace_dir = workspace_dir
        os.makedirs(workspace_dir, exist_ok=True)

    def _resolve_filename(self, filename: str) -> str:
        return _FILE_ALIASES.get(filename, filename)

    def _filepath(self, filename: str) -> str:
        canonical = self._resolve_filename(filename)
        return os.path.join(self.workspace_dir, canonical)

    def load_file(self, filename: str) -> str:
        canonical = self._resolve_filename(filename)
        filepath = os.path.join(self.workspace_dir, canonical)
        if os.path.exists(filepath):
            with open(filepath, encoding="utf-8") as f:
                return f.read()
        alias_path = os.path.join(self.workspace_dir, filename)
        if filename != canonical and os.path.exists(alias_path):
            with open(alias_path, encoding="utf-8") as f:
                return f.read()
        return ""

    def save_file(self, filename: str, content: str) -> bool:
        try:
            filepath = self._filepath(filename)
            with open(filepath, "w", encoding="utf-8") as f:
                f.write(content)
            return True
        except OSError:
            return False

    def list_files(self) -> dict[str, dict[str, Any]]:
        result = {}
        for filename, description in self.WORKSPACE_FILES.items():
            filepath = os.path.join(self.workspace_dir, filename)
            exists = os.path.exists(filepath)
            size = os.path.getsize(filepath) if exists else 0
            result[filename] = {"description": description, "exists": exists, "size": size}
        return result

    def ensure_team_templates(self) -> list[str]:
        """Create missing team workspace files; return list of created filenames."""
        created: list[str] = []
        for filename, template in _TEAM_TEMPLATES.items():
            path = Path(self.workspace_dir) / filename
            if path.exists():
                continue
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(template.strip() + "\n", encoding="utf-8")
            created.append(filename)
        return created

    def memory_digest_for_prompt(self, *, max_lines: int = 30, max_chars: int = 4000) -> str:
        """Return a trimmed MEMORY.md excerpt for system prompt injection."""
        raw = self.load_file("MEMORY.md").strip()
        if not raw:
            return ""
        lines = raw.splitlines()
        bullet_lines = [line for line in lines if line.strip().startswith("-")]
        if bullet_lines:
            selected = bullet_lines[:max_lines]
            text = "\n".join(selected)
        else:
            text = "\n".join(lines[: max_lines + 5])
        if len(text) > max_chars:
            text = text[: max_chars - 3].rstrip() + "..."
        return f"## Long-term Memory (MEMORY.md excerpt)\n{text}"

    def build_system_context(self) -> str:
        parts: list[str] = []

        for key in ("SOUL.md", "TEAM.md", "RULES.md", "IDENTITY.md"):
            content = self.load_file(key).strip()
            if content:
                parts.append(content)

        memory_excerpt = self.memory_digest_for_prompt()
        if memory_excerpt:
            parts.append(memory_excerpt)

        return "\n\n".join(parts)
