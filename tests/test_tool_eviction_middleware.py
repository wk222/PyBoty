"""Tests for LCToolEvictionMiddleware."""

from __future__ import annotations

import pytest

try:
    from langchain_core.messages import ToolMessage

    _HAS_LC = True
except ImportError:
    _HAS_LC = False

from core.assets.tools.tool_eviction_middleware import LCToolEvictionMiddleware

pytestmark = pytest.mark.skipif(not _HAS_LC, reason="langchain not installed")


class TestEviction:
    def test_short_output_passes_through(self, tmp_path):
        mw = LCToolEvictionMiddleware(eviction_dir=str(tmp_path))
        request = type("R", (), {"name": "some_tool"})()
        msg = ToolMessage(content="short output", tool_call_id="tc1")
        result = mw._maybe_evict(request, msg)
        assert result.content == "short output"

    def test_large_output_evicted(self, tmp_path):
        mw = LCToolEvictionMiddleware(eviction_dir=str(tmp_path), max_output_chars=100)
        request = type("R", (), {"name": "some_tool"})()
        big_content = "x" * 500
        msg = ToolMessage(content=big_content, tool_call_id="tc1")
        result = mw._maybe_evict(request, msg)
        assert "truncated" in result.content
        assert result.tool_call_id == "tc1"
        evicted_files = list(tmp_path.iterdir())
        assert len(evicted_files) == 1
        assert evicted_files[0].read_text(encoding="utf-8") == big_content

    def test_excluded_tool_not_evicted(self, tmp_path):
        mw = LCToolEvictionMiddleware(eviction_dir=str(tmp_path), max_output_chars=10)
        request = type("R", (), {"name": "read_file"})()
        msg = ToolMessage(content="x" * 100, tool_call_id="tc1")
        result = mw._maybe_evict(request, msg)
        assert result.content == "x" * 100

    def test_command_passes_through(self, tmp_path):
        from langgraph.types import Command

        mw = LCToolEvictionMiddleware(eviction_dir=str(tmp_path))
        request = type("R", (), {"name": "tool"})()
        cmd = Command(update={"messages": []})
        result = mw._maybe_evict(request, cmd)
        assert isinstance(result, Command)

    def test_name_property(self, tmp_path):
        mw = LCToolEvictionMiddleware(eviction_dir=str(tmp_path))
        assert mw.name == "LCToolEvictionMiddleware"
