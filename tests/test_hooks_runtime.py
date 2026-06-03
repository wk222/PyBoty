from __future__ import annotations

from core.systems.runtime.hooks_runtime import HookPhase, create_default_hooks_runtime


def test_hooks_runtime_tightens_permission_verdict_in_plan_mode():
    runtime = create_default_hooks_runtime()

    result = runtime.run_phase(
        HookPhase.PERMISSION_DECISION,
        {
            "tool_name": "write_file",
            "projected_runtime_view": {
                "permission": {"mode": "plan"},
                "settings": {"permission_mode": "plan"},
            },
        },
    )

    assert result["verdict"] == "deny"
    assert "plan mode blocks mutations" in " ".join(result["reason_fragments"])


def test_hooks_runtime_biases_route_back_to_trunk_when_compacted():
    runtime = create_default_hooks_runtime()

    result = runtime.run_phase(
        HookPhase.ROUTE_SELECTION,
        {
            "projected_runtime_view": {
                "permission": {"mode": "plan"},
                "context_hygiene": {"summary_active": True},
                "isolation": {"multi_agent_ready": False},
            }
        },
    )

    assert result["force_trunk_first"] is True
    assert "tool_runtime_governance" in result["prefer_slots"]
    assert "subagent_runtime" in result["avoid_slots"]
