"""
LangChain tools for interacting with the Hierarchical Markdown Memory Garden.
"""

from __future__ import annotations

import json
from typing import Any

from langchain.tools import BaseTool
from pydantic import BaseModel, Field

from .markdown_garden import MarkdownGardenManager

class ListGardenNotesInput(BaseModel):
    sub_dir: str = Field(description="要列出的子目录（可选，默认为根目录）", default="")

class ListGardenNotesTool(BaseTool):
    name: str = "list_garden_notes"
    description: str = "列出记忆园林中所有的 Markdown 笔记及其路径。"
    args_schema: type[BaseModel] = ListGardenNotesInput
    manager: MarkdownGardenManager = Field(exclude=True)

    def _run(self, sub_dir: str = "") -> str:
        notes = self.manager.list_notes(sub_dir)
        if not notes:
            return "记忆园林中目前没有笔记。"
        return "记忆园林笔记列表：\n" + "\n".join(f"- {n}" for n in notes)

class ReadGardenNoteInput(BaseModel):
    note_path: str = Field(description="笔记的相对路径（例如 'engineering/api_contracts.md'）")

class ReadGardenNoteTool(BaseTool):
    name: str = "read_garden_note"
    description: str = "读取记忆园林中特定笔记的完整内容。"
    args_schema: type[BaseModel] = ReadGardenNoteInput
    manager: MarkdownGardenManager = Field(exclude=True)

    def _run(self, note_path: str) -> str:
        content = self.manager.read_note(note_path)
        if content is None:
            return f"错误：找不到笔记 '{note_path}'"
        return f"--- 笔记内容: {note_path} ---\n\n{content}"

class UpdateGardenNoteInput(BaseModel):
    note_path: str = Field(description="笔记的相对路径")
    content: str = Field(description="笔记的新内容或要追加的内容")
    append: bool = Field(description="是否追加内容而不是覆盖", default=True)

class UpdateGardenNoteTool(BaseTool):
    name: str = "update_garden_note"
    description: str = "创建或更新记忆园林中的笔记。建议在决策、规范或项目结束时使用。"
    args_schema: type[BaseModel] = UpdateGardenNoteInput
    manager: MarkdownGardenManager = Field(exclude=True)

    def _run(self, note_path: str, content: str, append: bool = True) -> str:
        self.manager.update_note(note_path, content, append=append)
        action = "已更新" if append else "已创建/覆盖"
        return f"✅ 笔记 '{note_path}' {action}。"

class SearchGardenInput(BaseModel):
    query: str = Field(description="搜索关键词")

class SearchGardenTool(BaseTool):
    name: str = "search_garden"
    description: str = "在记忆园林的所有笔记中进行关键词搜索。"
    args_schema: type[BaseModel] = SearchGardenInput
    manager: MarkdownGardenManager = Field(exclude=True)

    def _run(self, query: str) -> str:
        results = self.manager.search_notes(query)
        if not results:
            return f"未找到包含 '{query}' 的笔记。"
        
        output = [f"找到 {len(results)} 条结果："]
        for r in results:
            output.append(f"- {r['path']}: {r['snippet']}")
        return "\n".join(output)

class ReorganizeGardenInput(BaseModel):
    action: str = Field(description="操作类型: 'move' 或 'delete'")
    src_path: str = Field(description="源笔记路径", default="")
    dst_path: str = Field(description="目标笔记路径（仅 move 操作需要）", default="")

class ReorganizeGardenTool(BaseTool):
    name: str = "reorganize_garden"
    description: str = "重构记忆园林的结构。支持移动（重命名）笔记或删除陈旧笔记。"
    args_schema: type[BaseModel] = ReorganizeGardenInput
    manager: MarkdownGardenManager = Field(exclude=True)

    def _run(self, action: str, src_path: str = "", dst_path: str = "") -> str:
        if action == "delete":
            success = self.manager.delete_note(src_path)
            return f"{'✅' if success else '❌'} 笔记 '{src_path}' {'已删除' if success else '未找到'}。"
        elif action == "move":
            if not dst_path: return "错误：移动操作需要提供 dst_path。"
            success = self.manager.move_note(src_path, dst_path)
            return f"{'✅' if success else '❌'} 笔记 '{src_path}' {'已移动到 ' + dst_path if success else '移动失败'}。"
        return f"错误：未知操作 '{action}'。"

def get_garden_tools(
    workspace_dir: str = "workspace",
    summarize_fn: Any | None = None,
    max_note_chars: int = 8000
) -> list[BaseTool]:
    manager = MarkdownGardenManager(
        workspace_dir=workspace_dir,
        summarize_fn=summarize_fn,
        max_note_chars=max_note_chars
    )
    return [
        ListGardenNotesTool(manager=manager),
        ReadGardenNoteTool(manager=manager),
        UpdateGardenNoteTool(manager=manager),
        SearchGardenTool(manager=manager),
        ReorganizeGardenTool(manager=manager),
    ]
