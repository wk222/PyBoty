"""Tests for core.memory_tools — SearchMemoryTool and SaveMemoryTool."""

from __future__ import annotations

import json
from dataclasses import dataclass
from unittest.mock import MagicMock

from core.systems.memory.memory_tools import SaveMemoryTool, SearchMemoryTool, get_memory_tools


@dataclass
class MockMemoryEntry:
    content: str
    section: str
    relevance: float = 0.9
    memory_type: str = "session_note"


class TestSearchMemoryTool:
    def test_search_with_semantic_manager(self):
        manager = MagicMock()
        manager.search_memories.return_value = [
            MockMemoryEntry(content="User prefers dark mode", section="preferences"),
            MockMemoryEntry(content="Project uses Python 3.12", section="facts"),
        ]
        tool = SearchMemoryTool(memory_manager=manager)
        result = json.loads(tool._run(query="user preferences", top_k=3))
        assert result["success"] is True
        assert result["count"] == 2
        assert result["results"][0]["content"] == "User prefers dark mode"

    def test_search_with_basic_manager(self):
        manager = MagicMock(spec=["load"])
        manager.load.return_value = "# Facts\n- Python 3.12\n- User likes dark mode\n# Other\n- unrelated"
        tool = SearchMemoryTool(memory_manager=manager)
        result = json.loads(tool._run(query="python"))
        assert result["success"] is True
        assert result["count"] == 1
        assert "Python" in result["results"][0]["content"]

    def test_search_no_manager(self):
        tool = SearchMemoryTool()
        result = json.loads(tool._run(query="anything"))
        assert result["success"] is False
        assert "not configured" in result["error"]

    def test_schema_has_descriptions(self):
        from core.systems.memory.memory_tools import SearchMemoryInput

        schema = SearchMemoryInput.model_json_schema()
        props = schema["properties"]
        assert "description" in props["query"]
        assert "description" in props["top_k"]


class TestSaveMemoryTool:
    def test_save_success(self):
        manager = MagicMock()
        tool = SaveMemoryTool(memory_manager=manager)
        result = json.loads(tool._run(section="preferences", content="User prefers vim"))
        assert result["success"] is True
        assert result["section"] == "preferences"
        manager.append_memory.assert_called_once_with(section="preferences", content="User prefers vim")

    def test_save_typed_memory_success(self):
        manager = MagicMock()
        tool = SaveMemoryTool(memory_manager=manager)
        result = json.loads(
            tool._run(
                section="",
                content="User prefers concise updates",
                memory_type="user",
                verified=True,
            )
        )
        assert result["success"] is True
        assert result["memory_type"] == "user"
        manager.append_typed_memory.assert_called_once()

    def test_save_failure(self):
        manager = MagicMock()
        manager.append_memory.side_effect = OSError("disk full")
        tool = SaveMemoryTool(memory_manager=manager)
        result = json.loads(tool._run(section="facts", content="test"))
        assert result["success"] is False
        assert "disk full" in result["error"]

    def test_save_no_manager(self):
        tool = SaveMemoryTool()
        result = json.loads(tool._run(section="facts", content="test"))
        assert result["success"] is False


class TestGetMemoryTools:
    def test_returns_three_tools(self):
        manager = MagicMock()
        tools = get_memory_tools(manager)
        assert len(tools) == 3
        names = {t.name for t in tools}
        assert "search_memory" in names
        assert "save_memory" in names
        assert "forget_memory" in names
