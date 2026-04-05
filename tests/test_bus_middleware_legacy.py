from __future__ import annotations

from unittest.mock import MagicMock

from core.systems.middleware.middleware_stack import BusMiddleware


def test_bus_middleware_records_model_context_after_invoke():
    bus = MagicMock()
    middleware = BusMiddleware(bus)

    state = {"messages": [{"role": "user", "content": "hello"}]}
    middleware.before_invoke(state)
    middleware.after_invoke(state, {"response": "ok"})

    assert bus.share_context.call_count == 2
    first_call = bus.share_context.call_args_list[0]
    second_call = bus.share_context.call_args_list[1]
    assert first_call[0][0] == "last_invoke_duration_ms"
    assert second_call[0][0] == "last_model_call"
    assert second_call[0][1]["message_count"] == 1


def test_bus_middleware_wrap_tool_output_records_tool_invocation():
    bus = MagicMock()
    middleware = BusMiddleware(bus)

    output = middleware.wrap_tool_output("lookup", "result payload")

    assert output == "result payload"
    bus.record_invocation.assert_called_once()
    args = bus.record_invocation.call_args
    assert args[0][0] == "lookup"
    assert args[1]["success"] is True
    assert args[1]["source"] == "bus_middleware"
    assert args[1]["operation"] == "tool_output"
