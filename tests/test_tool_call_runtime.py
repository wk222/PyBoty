from __future__ import annotations

from types import SimpleNamespace

from langchain_core.messages import ToolMessage

from core.agent_control import AgentControlPolicy
from core.approval_queue import ApprovalQueue
from core.tool_call_runtime import ToolCallRuntime
from core.tool_control_runtime import ToolControlRuntime
from core.tool_delegation_runtime import DelegatedToolApprovalRuntime
from core.tool_dynamic_inventory import DynamicToolInventory
from core.tool_middleware_observability import ToolMiddlewareObservability


def test_tool_call_runtime_executes_low_risk_tool_call():
    inventory = DynamicToolInventory()
    control_runtime = ToolControlRuntime(
        control_policy=AgentControlPolicy.from_config({"mode": "open"}),
        approval_scope="root:test",
        observability=ToolMiddlewareObservability(
            max_recent_calls=8,
            stuck_loop_threshold=5,
            stuck_loop_kill_threshold=8,
        ),
    )
    delegated_runtime = DelegatedToolApprovalRuntime(
        approval_queue=ApprovalQueue(),
        approval_scope="root:test",
    )
    runtime = ToolCallRuntime(
        inventory=inventory,
        control_runtime=control_runtime,
        delegated_runtime=delegated_runtime,
    )
    request = SimpleNamespace(
        tool_call={
            "name": "lookup",
            "args": {"q": "release"},
            "id": "call_1",
        }
    )

    result = runtime.run_tool_call(
        request,
        lambda tool_request: ToolMessage(
            content=f"ok:{tool_request.tool_call['args']['q']}",
            tool_call_id="call_1",
            status="success",
        ),
    )

    assert isinstance(result, ToolMessage)
    assert result.content == "ok:release"
    assert control_runtime.get_usage_stats()["lookup"] == 1
