from __future__ import annotations

from core.agent_control import AgentControlPolicy, ToolRiskLevel


def test_strict_policy_blocks_mutation_and_delegation():
    policy = AgentControlPolicy.from_config({"mode": "strict"})

    create_tool = policy.evaluate_tool_call("create_custom_tool")
    delegate = policy.evaluate_tool_call("delegate_to_agent")
    list_workflows = policy.evaluate_tool_call("list_workflows")

    assert create_tool.allowed is False
    assert delegate.allowed is False
    assert list_workflows.allowed is True


def test_balanced_policy_marks_high_risk_tools_without_blocking():
    policy = AgentControlPolicy.from_config({"mode": "balanced"})

    decision = policy.evaluate_tool_call("create_agent")

    assert decision.allowed is True
    assert decision.requires_approval is True
    assert decision.risk_level == ToolRiskLevel.CRITICAL
    assert "agent-mutation" in decision.control_tags
