"""
长期记忆管理器

基于 MEMORY.md 文件实现跨会话的持久化记忆：
1. 从 MEMORY.md 加载历史记忆
2. 在对话结束后提取关键信息
3. 自动更新 MEMORY.md
"""

import os
from datetime import datetime


class MemoryManager:
    def __init__(self, workspace_dir: str = "workspace"):
        self.workspace_dir = workspace_dir
        self.memory_path = os.path.join(workspace_dir, "MEMORY.md")
        self._ensure_file()

    def _ensure_file(self):
        os.makedirs(self.workspace_dir, exist_ok=True)
        if not os.path.exists(self.memory_path):
            default_memory = (
                "# MEMORY — 长期记忆\n\n"
                "> 此文件由智能体自动维护，记录跨会话的重要信息。\n\n"
                "## 用户偏好\n\n"
                "## 重要事实\n\n"
                "## 已完成的项目\n\n"
                "## 学到的经验\n"
            )
            with open(self.memory_path, "w", encoding="utf-8") as f:
                f.write(default_memory)

    def load(self) -> str:
        try:
            with open(self.memory_path, encoding="utf-8") as f:
                return f.read()
        except Exception:
            return ""

    def append_memory(self, section: str, content: str):
        current = self.load()
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M")
        entry = f"\n- [{timestamp}] {content}"

        section_header = f"## {section}"
        if section_header in current:
            parts = current.split(section_header)
            if len(parts) >= 2:
                next_section = parts[1].find("\n## ")
                if next_section != -1:
                    insert_pos = len(parts[0]) + len(section_header) + next_section
                    current = current[:insert_pos] + entry + "\n" + current[insert_pos:]
                else:
                    current = current + entry + "\n"
            else:
                current += f"\n{section_header}{entry}\n"
        else:
            current += f"\n{section_header}{entry}\n"

        with open(self.memory_path, "w", encoding="utf-8") as f:
            f.write(current)

    def get_context_prompt(self) -> str:
        memory = self.load()
        if not memory or memory.strip() == "# MEMORY — 长期记忆":
            return ""

        lines = [line for line in memory.split("\n") if line.strip() and not line.startswith(">")]
        content_lines = [line for line in lines if not line.startswith("# MEMORY")]
        if not content_lines:
            return ""

        return "\n\n--- 长期记忆 ---\n" + "\n".join(content_lines[-50:])


def extract_key_facts(conversation: list[dict]) -> list[dict[str, str]]:
    facts = []
    for msg in conversation:
        content = msg.get("content", "")
        if msg.get("role") == "user":
            if any(kw in content for kw in ["我叫", "我是", "我的名字", "请记住", "记住"]):
                facts.append({"section": "用户偏好", "content": content[:200]})
            if any(kw in content for kw in ["项目", "任务", "完成了", "做完了"]):
                facts.append({"section": "已完成的项目", "content": content[:200]})
    return facts
