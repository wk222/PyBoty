"""Structured todo-list middleware for agent task tracking.

Inspired by DeepAgents / LangChain TodoListMiddleware: gives the agent a
``write_todos`` tool so it can maintain a structured task list across
turns.  The todo state is kept in the agent's LangGraph state under the
``_todos`` key (private, not propagated to subagents).

Unlike a full-blown TaskScheduler (which is for background cron-like
jobs), this is lightweight in-conversation task bookkeeping.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

try:
    from langchain.agents.middleware import AgentMiddleware
    from langchain.agents.middleware.types import ModelRequest, ModelResponse
    from langchain_core.tools import BaseTool, StructuredTool

    _HAS_LC = True
except ImportError:
    _HAS_LC = False
    AgentMiddleware = object  # type: ignore[assignment,misc]
    ModelRequest = object  # type: ignore[assignment,misc]
    ModelResponse = object  # type: ignore[assignment,misc]
    BaseTool = object  # type: ignore[assignment,misc]
    StructuredTool = None  # type: ignore[assignment,misc]

from .agent_prompt_middleware import append_to_system_message


@dataclass
class TodoItem:
    id: str
    content: str
    status: str = "pending"  # pending | in_progress | completed | cancelled


@dataclass
class TodoState:
    items: list[TodoItem] = field(default_factory=list)

    def upsert(self, items_data: list[dict[str, str]]) -> str:
        id_map = {item.id: item for item in self.items}
        for raw in items_data:
            tid = raw.get("id", "")
            if not tid:
                continue
            if tid in id_map:
                if "content" in raw:
                    id_map[tid].content = raw["content"]
                if "status" in raw:
                    id_map[tid].status = raw["status"]
            else:
                self.items.append(
                    TodoItem(
                        id=tid,
                        content=raw.get("content", ""),
                        status=raw.get("status", "pending"),
                    )
                )
        return self.render()

    def render(self) -> str:
        if not self.items:
            return "(no todos)"
        lines = []
        for t in self.items:
            mark = {"completed": "x", "cancelled": "~", "in_progress": ">"}.get(t.status, " ")
            lines.append(f"[{mark}] {t.id}: {t.content}")
        return "\n".join(lines)

    def build_projection(self, *, limit: int = 8) -> dict[str, object] | None:
        if not self.items:
            return None
        normalized = [
            {
                "id": item.id,
                "content": item.content,
                "status": item.status,
            }
            for item in self.items[-max(1, int(limit)) :]
        ]
        status_counts: dict[str, int] = {}
        for item in self.items:
            status_counts[item.status] = status_counts.get(item.status, 0) + 1
        active = status_counts.get("pending", 0) + status_counts.get("in_progress", 0)
        summary = (
            f"活跃 {active} 项 / 完成 {status_counts.get('completed', 0)} 项"
            if self.items
            else ""
        )
        return {
            "summary": summary,
            "items": normalized,
            "status_counts": status_counts,
        }


TODO_PROMPT = """## Task Management — write_todos

You have a `write_todos` tool for tracking multi-step tasks:
- Create todos ONCE at the start of complex work (3+ steps)
- Batch status updates: update multiple items in a single call
- Do NOT call write_todos between every tool call — only at key milestones
- Skip write_todos for simple tasks (< 3 steps)
"""


class TodoListMiddleware(AgentMiddleware if _HAS_LC else object):  # type: ignore[misc]
    """Provide a ``write_todos`` tool and inject current todos into the prompt."""

    def __init__(self, *, task_runtime: Any | None = None) -> None:
        self._state = TodoState()
        self._task_runtime = task_runtime
        self.tools: list[BaseTool] = [self._build_tool()]

    @property
    def name(self) -> str:
        return "TodoListMiddleware"

    def export_projection(self) -> dict[str, object] | None:
        return self._state.build_projection()

    def _build_tool(self) -> BaseTool:
        mw = self

        def write_todos(todos: list[dict[str, str]]) -> str:
            """Create or update todo items.

            Args:
                todos: list of dicts with keys id, content, status.
                    status is one of: pending, in_progress, completed, cancelled.
            """
            rendered = mw._state.upsert(todos)
            if mw._task_runtime is not None and hasattr(mw._task_runtime, "upsert_tasks"):
                try:
                    mw._task_runtime.upsert_tasks(todos, source="write_todos")
                except Exception:
                    pass
            return rendered

        return StructuredTool.from_function(
            name="write_todos",
            description=(
                "Create or update a structured todo list. Each item needs "
                "id (unique), content (description), and status "
                "(pending/in_progress/completed/cancelled)."
            ),
            func=write_todos,
        )

    def wrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], ModelResponse],
    ) -> ModelResponse:
        request = self._inject(request)
        return handler(request)

    async def awrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], Awaitable[ModelResponse]],
    ) -> ModelResponse:
        request = self._inject(request)
        return await handler(request)

    def _inject(self, request: ModelRequest) -> ModelRequest:
        parts = [TODO_PROMPT]
        rendered = self._state.render()
        if rendered != "(no todos)":
            parts.append(f"\n### Current Todos\n{rendered}")
        text = "\n".join(parts)
        return request.override(
            system_message=append_to_system_message(request.system_message, text),
        )
