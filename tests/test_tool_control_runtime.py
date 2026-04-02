from __future__ import annotations

import json

import pytest

from core.systems.governance.agent_control import AgentControlPolicy
from core.systems.governance.tool_control_runtime import ToolControlRuntime


def test_tool_control_runtime_blocks_disallowed_dynamic_tools():
    runtime = ToolControlRuntime(
        control_policy=AgentControlPolicy.from_config({"mode": "strict"}),
        approval_scope="root:test",
    )

    result = runtime.enforce_tool_call(
        tool_name="custom_lookup",
        tool_args={"city": "Shanghai"},
        tool_call_id="call_1",
        is_dynamic=True,
    )

    assert result is not None
    payload = json.loads(result.content)
    assert payload["error"].startswith("CONTROL_POLICY_BLOCKED:")
    assert payload["tool_name"] == "custom_lookup"


def test_tool_control_runtime_reject_decision_returns_error_tool_message():
    runtime = ToolControlRuntime(
        control_policy=AgentControlPolicy.from_config({"mode": "balanced"}),
        approval_scope="root:test",
    )
    control_decision = runtime.control_policy.evaluate_tool_call("create_agent", is_dynamic=False)

    processed_tool_call, tool_message = runtime.apply_approval_decision(
        tool_call={
            "name": "create_agent",
            "args": {"agent_name": "helper"},
            "id": "call_1",
        },
        decision={"type": "reject", "message": "not allowed"},
        control_decision=control_decision,
    )

    assert processed_tool_call is None
    assert tool_message is not None
    assert tool_message.status == "error"
    assert tool_message.content == "not allowed"


def test_tool_control_runtime_validates_resume_decision_count():
    with pytest.raises(ValueError, match="decision count mismatch"):
        ToolControlRuntime.extract_resume_decisions(
            {"decisions": [{"type": "approve"}]},
            expected_count=2,
        )
