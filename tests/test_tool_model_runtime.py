from __future__ import annotations

import asyncio
from types import SimpleNamespace

from core.assets.tools.tool_model_runtime import ToolModelHookRuntime


class _ControlRuntimeStub:
    def __init__(self, approval_update=None):
        self.approval_update = approval_update
        self.calls: list[dict[str, object]] = []

    def interrupt_for_pending_approvals(self, **kwargs):
        self.calls.append(kwargs)
        return self.approval_update


def test_tool_model_hook_runtime_falls_back_when_refresh_fails():
    class Inventory:
        def __init__(self):
            self.fallback_called = False

        def pop_mutation_notice(self):
            return "builder_tool"

        def list_dynamic_tools(self):
            return [SimpleNamespace(name="builder_tool")]

        def refresh(self, dynamic_tools):
            raise RuntimeError("boom")

        def fallback_to_base_tools(self):
            self.fallback_called = True

    inventory = Inventory()
    runtime = ToolModelHookRuntime(inventory=inventory, control_runtime=_ControlRuntimeStub())

    runtime.before_model()

    assert inventory.fallback_called is True


def test_tool_model_hook_runtime_injects_tools_via_inventory():
    class Inventory:
        def inject_tools(self, request):
            request.tools.append("dynamic_lookup")
            return request, 1

    request = SimpleNamespace(tools=[])
    runtime = ToolModelHookRuntime(inventory=Inventory(), control_runtime=_ControlRuntimeStub())

    updated_request = runtime.inject_tools(request)

    assert updated_request.tools == ["dynamic_lookup"]


def test_tool_model_hook_runtime_passes_dynamic_tool_names_to_control_runtime():
    class Inventory:
        def get_dynamic_tool_names(self):
            return {"dynamic_lookup"}

    control_runtime = _ControlRuntimeStub(approval_update={"messages": ["pause"]})
    runtime = ToolModelHookRuntime(inventory=Inventory(), control_runtime=control_runtime)
    last_message = SimpleNamespace(tool_calls=[{"name": "create_agent", "args": {"agent_name": "helper"}, "id": "1"}])

    result = runtime.after_model({"messages": [last_message]})

    assert result == {"messages": ["pause"]}
    assert control_runtime.calls[0]["last_message"] is last_message
    assert control_runtime.calls[0]["dynamic_tool_names"] == {"dynamic_lookup"}


def test_tool_model_hook_runtime_returns_none_without_messages():
    runtime = ToolModelHookRuntime(inventory=object(), control_runtime=_ControlRuntimeStub())

    assert runtime.after_model({"messages": []}) is None


def test_tool_model_hook_runtime_wrap_model_call_uses_injected_request():
    class Inventory:
        def inject_tools(self, request):
            request.tools.append("dynamic_lookup")
            return request, 1

    runtime = ToolModelHookRuntime(inventory=Inventory(), control_runtime=_ControlRuntimeStub())
    request = SimpleNamespace(tools=[])

    updated = runtime.wrap_model_call(request, lambda incoming: incoming)

    assert updated.tools == ["dynamic_lookup"]


def test_tool_model_hook_runtime_wrap_model_call_async_survives_injection_failure():
    class Inventory:
        def inject_tools(self, request):
            raise RuntimeError("boom")

    runtime = ToolModelHookRuntime(inventory=Inventory(), control_runtime=_ControlRuntimeStub())
    request = SimpleNamespace(tools=[])

    async def invoke():
        return await runtime.wrap_model_call_async(request, lambda incoming: asyncio.sleep(0, result=incoming))

    updated = asyncio.run(invoke())

    assert updated is request
