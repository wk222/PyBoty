"""Tests for LCBusMiddleware."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

try:
    from langchain_core.messages import ToolMessage
    from langgraph.types import Command

    _HAS_LC = True
except ImportError:
    _HAS_LC = False

from core.systems.bus.lc_bus_middleware import LCBusMiddleware

pytestmark = pytest.mark.skipif(not _HAS_LC, reason="langchain not installed")


class TestBusModelCall:
    def test_records_duration(self):
        bus = MagicMock()
        mw = LCBusMiddleware(bus)
        request = MagicMock()
        request.messages = []

        def handler(req):
            return MagicMock()

        mw.wrap_model_call(request, handler)
        bus.share_context.assert_called_once()
        args = bus.share_context.call_args
        assert args[0][0] == "last_invoke_duration_ms"
        assert isinstance(args[0][1], float)
        assert args[1]["source"] == "bus_middleware"


class TestBusToolCall:
    def test_records_tool_invocation(self):
        bus = MagicMock()
        mw = LCBusMiddleware(bus)
        request = MagicMock()
        request.name = "my_tool"
        tool_msg = ToolMessage(content="ok", tool_call_id="tc1")

        def handler(req):
            return tool_msg

        result = mw.wrap_tool_call(request, handler)
        assert result is tool_msg
        bus.record_invocation.assert_called_once()
        args = bus.record_invocation.call_args
        assert args[0][0] == "my_tool"
        assert args[1]["success"] is True

    def test_records_command_as_non_success(self):
        bus = MagicMock()
        mw = LCBusMiddleware(bus)
        request = MagicMock()
        request.name = "tool"
        cmd = Command(update={"messages": []})

        def handler(req):
            return cmd

        mw.wrap_tool_call(request, handler)
        args = bus.record_invocation.call_args
        assert args[1]["success"] is False


class TestBusProperties:
    def test_name(self):
        mw = LCBusMiddleware(MagicMock())
        assert mw.name == "LCBusMiddleware"
