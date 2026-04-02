"""
工作空间管理器 (WorkspaceManager)

统一管理所有工作空间文件：
- SOUL.md: 智能体人格
- IDENTITY.md: 用户身份
- MEMORY.md: 长期记忆
- SCHEDULE.md: 定时任务
- skills/: 技能目录
"""

import os
from typing import Any


class WorkspaceManager:
    WORKSPACE_FILES = {
        "SOUL.md": "智能体人格定义",
        "IDENTITY.md": "用户身份信息",
        "MEMORY.md": "长期记忆",
        "SCHEDULE.md": "定时任务配置",
    }

    def __init__(self, workspace_dir: str = "workspace"):
        self.workspace_dir = workspace_dir
        os.makedirs(workspace_dir, exist_ok=True)

    def load_file(self, filename: str) -> str:
        filepath = os.path.join(self.workspace_dir, filename)
        if os.path.exists(filepath):
            with open(filepath, encoding="utf-8") as f:
                return f.read()
        return ""

    def save_file(self, filename: str, content: str) -> bool:
        try:
            filepath = os.path.join(self.workspace_dir, filename)
            with open(filepath, "w", encoding="utf-8") as f:
                f.write(content)
            return True
        except Exception:
            return False

    def list_files(self) -> dict[str, dict[str, Any]]:
        result = {}
        for filename, description in self.WORKSPACE_FILES.items():
            filepath = os.path.join(self.workspace_dir, filename)
            exists = os.path.exists(filepath)
            size = os.path.getsize(filepath) if exists else 0
            result[filename] = {"description": description, "exists": exists, "size": size}
        return result

    def build_system_context(self) -> str:
        parts = []

        soul = self.load_file("SOUL.md")
        if soul:
            parts.append(soul)

        identity = self.load_file("IDENTITY.md")
        if identity:
            parts.append(identity)

        return "\n\n".join(parts)
