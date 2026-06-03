from __future__ import annotations

import pytest

from core.systems.runtime.trusted_settings import build_trusted_settings_bundle


def test_trusted_settings_runtime_permission_updates_stay_on_session_layer():
    bundle = build_trusted_settings_bundle(
        user_values={"permission": {"mode": "default"}},
        session_values={"permission": {"mode": "default", "rules": {}}},
    )

    updated = bundle.with_permission_source(
        source="session",
        mode="plan",
        rules={"read_file": {"verdict": "ask", "reason": "review"}},
    )

    assert updated.effective["permission"]["mode"] == "plan"
    assert updated.get_layer("session").values["permission"]["rules"]["read_file"]["verdict"] == "ask"
    assert updated.get_layer("user").values["permission"]["mode"] == "default"
    assert updated.build_projection()["mutation_policy"]["runtime_writable_sources"] == ["managed_policy", "session"]


def test_trusted_settings_rejects_runtime_update_to_managed_source():
    bundle = build_trusted_settings_bundle()

    with pytest.raises(PermissionError):
        bundle.with_permission_source(
            source="project",
            mode="plan",
        )

def test_managed_policy_authoring_methods():
    bundle = build_trusted_settings_bundle()
    bundle = bundle.with_managed_policy(domain="custom_domain", policy={"flag": True})
    assert bundle.effective["custom_domain"]["flag"] is True

    bundle = bundle.with_agent_control_policy(
        mode="strict",
        blocked_tools=["tool_a", "tool_b"],
        max_subagent_depth=5,
    )
    assert bundle.effective["agent_control"]["mode"] == "strict"
    assert bundle.effective["agent_control"]["blocked_tools"] == ["tool_a", "tool_b"]
    assert bundle.effective["agent_control"]["max_subagent_depth"] == 5
    assert bundle.provenance["agent_control.mode"] == "managed_policy"

