"""Tests for PatchToolCallsMiddleware."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from core.patch_tool_calls import PatchToolCallsMiddleware

try:
    from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

    _HAS_LC = True
except ImportError:
    _HAS_LC = False

pytestmark = pytest.mark.skipif(not _HAS_LC, reason="langchain not installed")


def _ai_with_tool_calls(tool_calls: list[dict]) -> AIMessage:
    return AIMessage(content="", tool_calls=tool_calls)


class TestPatchStaticMethod:
    def test_empty_messages_returns_none(self):
        assert PatchToolCallsMiddleware._patch([]) is None

    def test_no_dangling_returns_none(self):
        ai = _ai_with_tool_calls([{"id": "tc1", "name": "foo", "args": {}}])
        tool_msg = ToolMessage(content="ok", tool_call_id="tc1")
        assert PatchToolCallsMiddleware._patch([ai, tool_msg]) is None

    def test_single_dangling_gets_patched(self):
        ai = _ai_with_tool_calls([{"id": "tc1", "name": "bar", "args": {}}])
        human = HumanMessage(content="new message")
        result = PatchToolCallsMiddleware._patch([ai, human])
        assert result is not None
        assert len(result) == 3
        assert isinstance(result[1], ToolMessage)
        assert result[1].tool_call_id == "tc1"
        assert "cancelled" in result[1].content

    def test_multiple_dangling_calls(self):
        ai = _ai_with_tool_calls(
            [
                {"id": "tc1", "name": "a", "args": {}},
                {"id": "tc2", "name": "b", "args": {}},
            ]
        )
        result = PatchToolCallsMiddleware._patch([ai])
        assert result is not None
        patched_tool_msgs = [m for m in result if isinstance(m, ToolMessage)]
        assert len(patched_tool_msgs) == 2
        ids = {m.tool_call_id for m in patched_tool_msgs}
        assert ids == {"tc1", "tc2"}

    def test_partial_dangling(self):
        ai = _ai_with_tool_calls(
            [
                {"id": "tc1", "name": "a", "args": {}},
                {"id": "tc2", "name": "b", "args": {}},
            ]
        )
        tool_msg = ToolMessage(content="ok", tool_call_id="tc1")
        result = PatchToolCallsMiddleware._patch([ai, tool_msg])
        assert result is not None
        patched = [m for m in result if isinstance(m, ToolMessage) and "cancelled" in m.content]
        assert len(patched) == 1
        assert patched[0].tool_call_id == "tc2"

    def test_ai_without_tool_calls_ignored(self):
        msgs = [AIMessage(content="hello"), HumanMessage(content="hi")]
        assert PatchToolCallsMiddleware._patch(msgs) is None

    def test_wrap_model_call_patches_request(self):
        ai = _ai_with_tool_calls([{"id": "tc1", "name": "x", "args": {}}])
        request = MagicMock()
        request.messages = [ai, HumanMessage(content="y")]
        captured = {}

        def handler(req):
            captured["messages"] = list(req.messages)
            return "response"

        request.override = lambda messages: MagicMock(messages=messages)
        mw = PatchToolCallsMiddleware()
        mw.wrap_model_call(request, handler)
        assert len(captured["messages"]) == 3


class TestPatchEdgeCases:
    def test_tool_call_with_empty_id(self):
        ai = _ai_with_tool_calls([{"id": "", "name": "z", "args": {}}])
        result = PatchToolCallsMiddleware._patch([ai])
        assert result is not None
        patched = [m for m in result if isinstance(m, ToolMessage)]
        assert len(patched) == 1

    def test_interleaved_ai_messages(self):
        ai1 = _ai_with_tool_calls([{"id": "tc1", "name": "a", "args": {}}])
        tool1 = ToolMessage(content="ok", tool_call_id="tc1")
        ai2 = _ai_with_tool_calls([{"id": "tc2", "name": "b", "args": {}}])
        result = PatchToolCallsMiddleware._patch([ai1, tool1, ai2])
        assert result is not None
        patched = [m for m in result if isinstance(m, ToolMessage) and "cancelled" in m.content]
        assert len(patched) == 1
        assert patched[0].tool_call_id == "tc2"
