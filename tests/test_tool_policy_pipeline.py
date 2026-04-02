from __future__ import annotations

from core.systems.governance import (
    AgentControlPolicy,
    PathPolicyStage,
    RateLimitStage,
    ToolPolicyContext,
    ToolRiskLevel,
    build_default_tool_policy_pipeline,
)


def _context(
    *,
    tool_name: str = "write_file",
    tool_args: dict | None = None,
    recent_calls: int = 0,
    policy: AgentControlPolicy | None = None,
) -> ToolPolicyContext:
    return ToolPolicyContext(
        tool_name=tool_name,
        tool_args=tool_args or {},
        tool_call_id="call_1",
        is_dynamic=False,
        approval_scope="root:test",
        control_policy=policy or AgentControlPolicy.from_config({"mode": "balanced"}),
        recent_calls=recent_calls,
    )


def test_path_policy_blocks_parent_traversal():
    stage = PathPolicyStage(allowed_roots=("C:/workspace",))

    decision = stage.evaluate(_context(tool_args={"path": "../secrets.txt"}))

    assert decision is not None
    assert decision.allowed is False
    assert decision.risk_level == ToolRiskLevel.CRITICAL
    assert "path-traversal" in decision.control_tags


def test_path_policy_blocks_absolute_path_outside_allowed_roots(tmp_path):
    allowed = tmp_path / "workspace"
    allowed.mkdir()
    outside = tmp_path / "outside.txt"

    stage = PathPolicyStage(allowed_roots=(str(allowed),))
    decision = stage.evaluate(_context(tool_args={"path": str(outside)}))

    assert decision is not None
    assert decision.allowed is False
    assert "path-outside-root" in decision.control_tags


def test_rate_limit_stage_blocks_after_threshold():
    stage = RateLimitStage(max_calls_per_tool=2)

    decision = stage.evaluate(_context(recent_calls=2))

    assert decision is not None
    assert decision.allowed is False
    assert decision.risk_level == ToolRiskLevel.HIGH
    assert "rate-limit" in decision.control_tags


def test_default_pipeline_preserves_approval_required_risk():
    policy = AgentControlPolicy.from_config({"mode": "balanced"})
    pipeline = build_default_tool_policy_pipeline(
        control_policy=policy,
        allowed_roots=(),
        max_calls_per_tool=policy.max_recent_tool_calls,
    )

    decision = pipeline.evaluate(
        _context(
            tool_name="create_agent",
            tool_args={"agent_name": "helper"},
            policy=policy,
        )
    )

    assert decision.allowed is True
    assert decision.requires_approval is True
    assert decision.risk_level == ToolRiskLevel.CRITICAL
    assert "agent-mutation" in decision.control_tags


def test_default_pipeline_blocks_absolute_path_when_roots_provided(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    outside = tmp_path / "system.txt"
    pipeline = build_default_tool_policy_pipeline(
        control_policy=AgentControlPolicy.from_config({"mode": "open"}),
        allowed_roots=[str(workspace)],
        max_calls_per_tool=10,
    )

    decision = pipeline.evaluate(
        _context(
            tool_name="write_file",
            tool_args={"path": str(outside)},
            policy=AgentControlPolicy.from_config({"mode": "open"}),
        )
    )

    assert decision.allowed is False
    assert decision.risk_level == ToolRiskLevel.CRITICAL
    assert "path-policy" in decision.control_tags
