"""Tests for TodoListMiddleware."""

from __future__ import annotations

from core.systems.middleware.todo_middleware import TodoItem, TodoListMiddleware, TodoState


class TestTodoState:
    def test_upsert_creates_items(self):
        state = TodoState()
        result = state.upsert(
            [
                {"id": "1", "content": "task A", "status": "pending"},
                {"id": "2", "content": "task B", "status": "in_progress"},
            ]
        )
        assert len(state.items) == 2
        assert "task A" in result
        assert "task B" in result

    def test_upsert_updates_existing(self):
        state = TodoState()
        state.upsert([{"id": "1", "content": "original", "status": "pending"}])
        state.upsert([{"id": "1", "status": "completed"}])
        assert state.items[0].status == "completed"
        assert state.items[0].content == "original"

    def test_upsert_skips_empty_id(self):
        state = TodoState()
        state.upsert([{"id": "", "content": "no id"}])
        assert len(state.items) == 0

    def test_render_empty(self):
        state = TodoState()
        assert state.render() == "(no todos)"

    def test_render_status_markers(self):
        state = TodoState()
        state.items = [
            TodoItem(id="1", content="pending", status="pending"),
            TodoItem(id="2", content="progress", status="in_progress"),
            TodoItem(id="3", content="done", status="completed"),
            TodoItem(id="4", content="skip", status="cancelled"),
        ]
        rendered = state.render()
        assert "[ ]" in rendered
        assert "[>]" in rendered
        assert "[x]" in rendered
        assert "[~]" in rendered

    def test_upsert_merge_preserves_order(self):
        state = TodoState()
        state.upsert(
            [
                {"id": "a", "content": "first"},
                {"id": "b", "content": "second"},
            ]
        )
        state.upsert(
            [
                {"id": "c", "content": "third"},
                {"id": "a", "status": "completed"},
            ]
        )
        assert len(state.items) == 3
        assert state.items[0].id == "a"
        assert state.items[0].status == "completed"
        assert state.items[2].id == "c"


class TestTodoMiddleware:
    def test_has_write_todos_tool(self):
        mw = TodoListMiddleware()
        assert len(mw.tools) == 1
        assert mw.tools[0].name == "write_todos"

    def test_tool_creates_todos(self):
        mw = TodoListMiddleware()
        tool = mw.tools[0]
        result = tool.invoke(
            {
                "todos": [
                    {"id": "1", "content": "test task", "status": "pending"},
                ]
            }
        )
        assert "test task" in result

    def test_name_property(self):
        mw = TodoListMiddleware()
        assert mw.name == "TodoListMiddleware"
