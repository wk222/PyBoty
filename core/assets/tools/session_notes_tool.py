"""Tools for managing session-level context notes and team shared state."""

from __future__ import annotations

import json
from typing import Any

from langchain.tools import BaseTool
from pydantic import BaseModel, Field


class TakeNoteInput(BaseModel):
    note: str = Field(description="要记录的笔记内容，简明扼要地描述当前发现、决策或进度。")


class TakeNoteTool(BaseTool):
    """Tool for agents to take notes that persist in the session context."""

    name: str = "take_note"
    description: str = (
        "记录一条关于当前任务的笔记。笔记会持久化在当前智能体的上下文中，并在后续步骤中可见。 "
        "适用于记录重要的发现、中间结论、待办事项或需要保留的关键信息。"
    )
    args_schema: type[BaseModel] = TakeNoteInput
    runtime_context: dict[str, Any] = Field(default_factory=dict, exclude=True)

    def _run(self, note: str) -> str:
        note_text = str(note).strip()
        if not note_text:
            return "笔记内容不能为空。"

        notes = self.runtime_context.setdefault("context_notes_buffer", [])
        if isinstance(notes, list):
            notes.append(note_text)
        
        return f"✅ 笔记已记录：{note_text[:50]}{'...' if len(note_text) > 50 else ''}"


class AddTeamNoteInput(BaseModel):
    note: str = Field(description="要向团队共享内存中添加的笔记内容。")


class AddTeamNoteTool(BaseTool):
    """Tool for agents to push notes directly to the shared team memory."""

    name: str = "add_team_note"
    description: str = (
        "向团队共享内存中添加一条笔记。该笔记会立刻对团队中所有其他正在运行的智能体以及协调者可见。 "
        "仅适用于需要跨智能体实时共享的关键结论、接口契约或阻塞问题。"
    )
    args_schema: type[BaseModel] = AddTeamNoteInput
    runtime_context: dict[str, Any] = Field(default_factory=dict, exclude=True)
    registry: Any = Field(default=None, exclude=True)

    def _run(self, note: str) -> str:
        note_text = str(note).strip()
        if not note_text:
            return "笔记内容不能为空。"

        team_key = str(self.runtime_context.get("team_key", "")).strip() or \
                   str(self.runtime_context.get("session_key", "")).strip() or \
                   str(self.runtime_context.get("owner_thread_id", "")).strip()
                   
        agent_name = str(self.runtime_context.get("current_agent_name", "unknown")).strip()

        if not self.registry or not team_key:
            # Fallback to local context notes if team memory is unavailable
            notes = self.runtime_context.setdefault("context_notes_buffer", [])
            if isinstance(notes, list):
                notes.append(note_text)
            return f"⚠️ 团队内存未就绪，笔记已降级记录到本地上下文：{note_text[:50]}..."

        self.registry.add_team_note(
            team_key=team_key,
            agent_name=agent_name,
            note=note_text
        )
        
        return f"✅ 团队笔记已同步：{note_text[:50]}{'...' if len(note_text) > 50 else ''}"


def get_session_note_tools(runtime_context: dict[str, Any] | None = None, registry: Any = None) -> list[BaseTool]:
    tools: list[BaseTool] = [TakeNoteTool(runtime_context=runtime_context or {})]
    if registry is not None:
        tools.append(AddTeamNoteTool(runtime_context=runtime_context or {}, registry=registry))
    return tools

