"""
Hierarchical Markdown Memory Garden Manager.

Implements a directory-based long-term memory system where knowledge is stored
as structured Markdown files. This allows for:
1. Human-readable and editable knowledge base.
2. Hierarchical organization (folders and sub-notes).
3. Recursive summarization (summary notes linking to detail notes).
"""

from __future__ import annotations

import os
import logging
from pathlib import Path
from datetime import datetime
from dataclasses import dataclass
from typing import Any, Callable

logger = logging.getLogger(__name__)

@dataclass
class GardenNote:
    """A single note in the memory garden."""
    path: str
    content: str
    title: str = ""
    summary: str = ""
    last_updated: str = ""


class MarkdownGardenManager:
    """Manages a collection of Markdown files acting as long-term memory."""

    def __init__(
        self, 
        workspace_dir: str = "workspace",
        summarize_fn: Callable[[str], str] | None = None,
        max_note_chars: int = 8000
    ):
        self.garden_root = Path(workspace_dir) / "memory"
        self.summarize_fn = summarize_fn
        self.max_note_chars = max_note_chars
        self._ensure_garden()

    def _ensure_garden(self):
        """Initialize the garden structure if it doesn't exist."""
        self.garden_root.mkdir(parents=True, exist_ok=True)
        index_path = self.garden_root / "index.md"
        if not index_path.exists():
            default_index = (
                "# 🧠 Memory Garden — 记忆园林\n\n"
                "> 此目录存放沉淀后的长期知识与决策。智能体通过层级化的 Markdown 文件维护此园林。\n\n"
                "## 导航\n"
                "- [用户偏好](./preferences.md)\n"
                "- [工程规范](./engineering/index.md)\n"
                "- [项目沉淀](./projects/index.md)\n"
            )
            index_path.write_text(default_index, encoding="utf-8")
            
        # Create sub-folders
        (self.garden_root / "engineering").mkdir(exist_ok=True)
        (self.garden_root / "projects").mkdir(exist_ok=True)

    def list_notes(self, sub_dir: str = "") -> list[str]:
        """List all markdown notes in the garden or a sub-directory."""
        target_dir = self.garden_root / sub_dir.lstrip("/")
        if not target_dir.exists():
            return []
        
        notes = []
        for p in target_dir.glob("**/*.md"):
            rel_path = p.relative_to(self.garden_root)
            notes.append(str(rel_path).replace("\\", "/"))
        return sorted(notes)

    def read_note(self, note_path: str) -> str | None:
        """Read the content of a specific note."""
        full_path = self.garden_root / note_path.lstrip("/")
        if not full_path.exists() or not full_path.is_file():
            return None
        return full_path.read_text(encoding="utf-8")

    def update_note(self, note_path: str, content: str, append: bool = False) -> bool:
        """Create or update a note in the garden."""
        full_path = self.garden_root / note_path.lstrip("/")
        full_path.parent.mkdir(parents=True, exist_ok=True)
        
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M")
        
        if append and full_path.exists():
            existing = full_path.read_text(encoding="utf-8")
            new_content = existing.rstrip() + f"\n\n### 更新 ({timestamp})\n{content}\n"
            full_path.write_text(new_content, encoding="utf-8")
        else:
            # If creating a new file, ensure it has a title
            if not content.startswith("# "):
                title = note_path.split("/")[-1].replace(".md", "").capitalize()
                content = f"# {title}\n\n> 创建时间: {timestamp}\n\n{content}"
            full_path.write_text(content, encoding="utf-8")
            
        # Check for summarization
        self._check_and_summarize(note_path)
        return True

    def _check_and_summarize(self, note_path: str):
        """If a note exceeds max_note_chars, trigger recursive summarization."""
        if not self.summarize_fn:
            return

        full_path = self.garden_root / note_path.lstrip("/")
        if not full_path.exists():
            return

        content = full_path.read_text(encoding="utf-8")
        if len(content) <= self.max_note_chars:
            return

        logger.info("Triggering recursive summarization for: %s", note_path)
        self.summarize_note(note_path)

    def summarize_note(self, note_path: str) -> bool:
        """
        Perform recursive summarization:
        1. Extract current content.
        2. Move current content to an archive sub-note.
        3. Generate a summary of the archived content.
        4. Rewrite the main note with the summary and a link to the archive.
        """
        if not self.summarize_fn:
            return False

        full_path = self.garden_root / note_path.lstrip("/")
        if not full_path.exists():
            return False

        content = full_path.read_text(encoding="utf-8")
        
        # 1. Generate summary
        summary_prompt = (
            f"请为以下 Markdown 笔记生成一个详尽但精炼的摘要。保留所有核心结论、参数、契约和关键决策。\n"
            f"目标是让读者即使不看原件也能掌握核心上下文。\n\n"
            f"笔记内容：\n{content}"
        )
        summary = self.summarize_fn(summary_prompt)
        
        # 2. Archive details
        timestamp_slug = datetime.now().strftime("%Y%m%d_%H%M%S")
        archive_name = note_path.replace(".md", f"_archive_{timestamp_slug}.md")
        archive_path = self.garden_root / archive_name
        
        # Add back-link to archive
        archive_content = (
            f"# Archive: {note_path} ({timestamp_slug})\n\n"
            f"> 这是原始笔记的备份。摘要已更新在主笔记中。\n\n"
            f"{content}"
        )
        archive_path.write_text(archive_content, encoding="utf-8")
        
        # 3. Update main note
        new_main_content = (
            f"# {note_path.split('/')[-1].replace('.md', '').capitalize()} (Summarized)\n\n"
            f"> 摘要更新时间: {datetime.now().strftime('%Y-%m-%d %H:%M')}\n"
            f"> 原始详情已移至: [{archive_name}](./{archive_name.split('/')[-1]})\n\n"
            f"## 核心摘要\n\n{summary}\n"
        )
        full_path.write_text(new_main_content, encoding="utf-8")
        
        logger.info("Note %s summarized successfully. Archive: %s", note_path, archive_name)
        return True

    def search_notes(self, query: str) -> list[dict[str, Any]]:
        """Simple keyword search across notes."""
        results = []
        query_lower = query.lower()
        for p in self.garden_root.glob("**/*.md"):
            content = p.read_text(encoding="utf-8")
            if query_lower in content.lower():
                rel_path = p.relative_to(self.garden_root)
                # Find first occurrence for snippet
                idx = content.lower().find(query_lower)
                start = max(0, idx - 50)
                end = min(len(content), idx + 100)
                snippet = content[start:end].replace("\n", " ")
                results.append({
                    "path": str(rel_path).replace("\\", "/"),
                    "snippet": f"...{snippet}..."
                })
        return results

    def delete_note(self, note_path: str) -> bool:
        """Delete a note from the garden."""
        full_path = self.garden_root / note_path.lstrip("/")
        if full_path.exists() and full_path.is_file():
            full_path.unlink()
            return True
        return False

    def move_note(self, src_path: str, dst_path: str) -> bool:
        """Move or rename a note in the garden."""
        src = self.garden_root / src_path.lstrip("/")
        dst = self.garden_root / dst_path.lstrip("/")
        if src.exists() and src.is_file():
            dst.parent.mkdir(parents=True, exist_ok=True)
            src.rename(dst)
            return True
        return False


# Keep old name but prefer shorter one for public API
MarkdownGarden = MarkdownGardenManager
