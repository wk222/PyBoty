import os
import yaml
from typing import Dict, Optional

class MarkdownSkillLoader:
    """
    软技能加载器：负责加载和解析 Markdown 格式的 SOP 和领域经验。
    这些技能仅作为上下文注入，不包含可执行代码。
    """
    def __init__(self, skills_dir: str):
        self.skills_dir = skills_dir
        self.skills: Dict[str, dict] = {}
        self._load_skills()

    def _load_skills(self) -> None:
        if not os.path.exists(self.skills_dir):
            return
            
        for filename in os.listdir(self.skills_dir):
            if not filename.endswith(".md"):
                continue
                
            filepath = os.path.join(self.skills_dir, filename)
            try:
                with open(filepath, 'r', encoding='utf-8') as f:
                    content = f.read()
                    
                # 解析 Markdown 的 YAML Frontmatter
                if content.startswith("---"):
                    parts = content.split("---", 2)
                    if len(parts) >= 3:
                        meta = yaml.safe_load(parts[1]) or {}
                        body = parts[2].strip()
                        skill_name = meta.get('name', filename.replace('.md', ''))
                        self.skills[skill_name] = {
                            "description": meta.get('description', ''),
                            "instructions": body
                        }
            except Exception as e:
                print(f"[MarkdownSkillLoader] Failed to load skill {filename}: {e}")

    def get_skill_content(self, skill_name: str) -> Optional[str]:
        """供 LLM 按需读取 SOP 时使用"""
        if skill_name in self.skills:
            return f"<skill_instructions name='{skill_name}'>\n{self.skills[skill_name]['instructions']}\n</skill_instructions>"
        return None

    def get_all_skills_summary(self) -> str:
        """返回所有可用软技能的摘要，供注入到 System Prompt"""
        if not self.skills:
            return ""
        summary = "可用软技能 (SOP/经验):\n"
        for name, data in self.skills.items():
            summary += f"- {name}: {data['description']}\n"
        return summary
