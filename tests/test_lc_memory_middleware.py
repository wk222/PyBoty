"""Tests for LCMemoryMiddleware."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

try:
    from langchain_core.messages import AIMessage, HumanMessage

    _HAS_LC = True
except ImportError:
    _HAS_LC = False

from core.lc_memory_middleware import LCMemoryMiddleware

pytestmark = pytest.mark.skipif(not _HAS_LC, reason="langchain not installed")


class TestMemoryExtraction:
    def test_extracts_from_two_messages_legacy(self):
        """Test legacy extraction path (memory manager without auto_capture)."""
        memory = MagicMock(spec=["append_memory", "load"])
        mw = LCMemoryMiddleware(memory, auto_capture=True)
        messages = [
            HumanMessage(content="Remember that the API key is xyz123"),
            AIMessage(content="Got it, I'll remember the API key"),
        ]
        with patch(
            "core.memory_manager.extract_key_facts",
            return_value=[
                {"section": "credentials", "content": "API key is xyz123"},
            ],
        ):
            mw._extract_memories(messages)
        memory.append_memory.assert_called_once_with("credentials", "API key is xyz123")

    def test_extracts_via_auto_capture(self):
        """Test auto_capture path (SemanticMemoryManager)."""
        memory = MagicMock()
        memory.auto_capture.return_value = ["fact1"]
        mw = LCMemoryMiddleware(memory, auto_capture=True)
        messages = [
            HumanMessage(content="Remember X"),
            AIMessage(content="Got it"),
        ]
        mw._extract_memories(messages)
        memory.auto_capture.assert_called_once()

    def test_skips_short_conversation(self):
        memory = MagicMock()
        mw = LCMemoryMiddleware(memory)
        mw._extract_memories([HumanMessage(content="hi")])
        memory.append_memory.assert_not_called()

    def test_skips_empty_messages(self):
        memory = MagicMock()
        mw = LCMemoryMiddleware(memory)
        mw._extract_memories([])
        memory.append_memory.assert_not_called()

    def test_handles_extraction_failure(self):
        memory = MagicMock(spec=["append_memory", "load"])
        mw = LCMemoryMiddleware(memory)
        messages = [
            HumanMessage(content="test"),
            AIMessage(content="response"),
        ]
        with patch("core.memory_manager.extract_key_facts", side_effect=RuntimeError("boom")):
            mw._extract_memories(messages)
        memory.append_memory.assert_not_called()

    def test_no_capture_when_disabled(self):
        memory = MagicMock()
        mw = LCMemoryMiddleware(memory, auto_capture=False)
        messages = [
            HumanMessage(content="Remember X"),
            AIMessage(content="Got it"),
        ]
        mw._extract_memories(messages)
        memory.auto_capture.assert_not_called()
        memory.append_memory.assert_not_called()

    def test_name_property(self):
        mw = LCMemoryMiddleware(MagicMock())
        assert mw.name == "LCMemoryMiddleware"
