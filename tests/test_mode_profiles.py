from __future__ import annotations

from core.modes import resolve_mode_profile


def test_assistant_mode_profile_disables_durable_and_app_matrix_modules():
    profile = resolve_mode_profile("assistant")

    assert profile.name == "assistant"
    assert profile.attach_admin_runtime_by_default is False
    assert profile.enables_durable_goal_loop is False
    assert profile.enables_app_orchestration is False
    assert profile.enables_app_topology_planning is False


def test_app_matrix_mode_profile_enables_app_orchestration_modules():
    profile = resolve_mode_profile("app_matrix")

    assert profile.name == "app_matrix"
    assert profile.attach_admin_runtime_by_default is True
    assert profile.enables_durable_goal_loop is True
    assert profile.enables_app_orchestration is True
    assert profile.enables_app_topology_planning is True
    assert "app_orchestration" in profile.enabled_capabilities()


def test_admin_alias_resolves_to_admin_profile():
    profile = resolve_mode_profile("admin")

    assert profile.name == "admin"
    assert profile.attach_admin_runtime_by_default is True
    assert profile.durable_runtime_dir == "admin"
