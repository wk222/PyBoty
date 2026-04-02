"""Todo Write Tool for Agent Self-Planning.

Inspired by DeepAgents, this tool allows an agent to maintain a persistent
checklist or todo list within its context window. This helps the agent
track progress on complex, multi-step tasks without losing focus.
"""

from __future__ import annotations

import json
from enum import Enum
from typing import Any

from langchain.tools import BaseTool
from pydantic import BaseModel, ConfigDict, Field


class TodoStatus(str, Enum):
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    CANCELLED = "cancelled"


class TodoItem(BaseModel):
    id: str = Field(description="Unique identifier for the TODO item")
    content: str = Field(description="The description/content of the todo item")
    status: TodoStatus = Field(description="The current status of the TODO item")


class TodoWriteInput(BaseModel):
    todos: list[TodoItem] = Field(description="Array of TODO items to update or create")
    merge: bool = Field(
        default=True,
        description="If true, merge with existing todos based on ID. If false, replace the entire list.",
    )


class TodoWriteTool(BaseTool):
    name: str = "todo_write"
    description: str = """Use this tool to create and manage a structured task list for your current session.
This helps you track progress, organize complex tasks, and demonstrate thoroughness.
When starting a complex multi-step task, call this tool first to outline your plan.
As you complete steps, call this tool again with merge=true to update the status of items to 'completed'."""
    args_schema: type[BaseModel] = TodoWriteInput
    model_config = ConfigDict(arbitrary_types_allowed=True)

    # In a real implementation, this state might be stored in the agent's memory or thread state.
    # For simplicity, we store it on the tool instance (which works if the tool is instantiated per-session).
    _current_todos: dict[str, TodoItem] = {}

    def _run(self, todos: list[TodoItem], merge: bool = True) -> str:
        if not merge:
            self._current_todos.clear()

        for todo in todos:
            self._current_todos[todo.id] = todo

        # Format the output nicely
        lines = ["Current Task List:"]
        for todo_id, todo in self._current_todos.items():
            status_mark = " "
            if todo.status == TodoStatus.COMPLETED:
                status_mark = "x"
            elif todo.status == TodoStatus.IN_PROGRESS:
                status_mark = "~"
            elif todo.status == TodoStatus.CANCELLED:
                status_mark = "-"
            
            lines.append(f"[{status_mark}] {todo.id}: {todo.content}")

        return "\n".join(lines)
