from __future__ import annotations

from core.systems.governance.permission_policy import (
    PermissionMode,
    PermissionPolicy,
    RuleVerdict,
)
from core.systems.runtime.trusted_settings import build_trusted_settings_bundle


def test_plan_mode_allows_read_only_web_fetch():
    policy = PermissionPolicy()
    policy.set_mode(PermissionMode.PLAN)

    assert policy.evaluate("web_fetch", "medium") == RuleVerdict.ALLOW
    assert policy.evaluate("read_file", "low") == RuleVerdict.ALLOW
    assert policy.evaluate("write_file", "medium") == RuleVerdict.DENY
    assert policy.evaluate("bash", "high") == RuleVerdict.DENY


def test_permission_projection_tracks_rules_and_recent_events():
    policy = PermissionPolicy()

    policy.set_mode(PermissionMode.PLAN)
    policy.add_rule("read_file", RuleVerdict.ASK, reason="confirm before reading", source="session")
    removed = policy.remove_rule("read_file")
    policy.add_rule("web_fetch", RuleVerdict.ALLOW, reason="research enabled", source="user")

    assert removed is True

    projection = policy.build_projection(limit=6)
    assert projection["mode"] == "plan"
    assert projection["rule_count"] == 1
    assert projection["rules"][0]["tool_name"] == "web_fetch"
    assert projection["rules"][0]["verdict"] == "allow"
    assert projection["summary"] == "mode=plan, 1 active rule"
    assert [event["action"] for event in projection["recent_events"]] == [
        "set_mode",
        "set_rule",
        "remove_rule",
        "set_rule",
    ]


def test_permission_tools_are_treated_as_write_like_in_plan_mode():
    policy = PermissionPolicy()
    policy.set_mode(PermissionMode.PLAN)

    assert policy.evaluate("set_permission_mode", "high") == RuleVerdict.DENY
    assert policy.evaluate("set_permission_rule", "medium") == RuleVerdict.DENY
    assert policy.evaluate("clear_permission_rules", "medium") == RuleVerdict.DENY
    assert policy.evaluate("get_permission_state", "low") == RuleVerdict.ALLOW


def test_permission_rule_precedence_prefers_session_over_project_and_user():
    policy = PermissionPolicy()
    policy.add_rule("read_file", RuleVerdict.ALLOW, source="user")
    policy.add_rule("read_file", RuleVerdict.DENY, source="project")
    policy.add_rule("read_file", RuleVerdict.ASK, source="session")

    assert policy.evaluate("read_file", "low") == RuleVerdict.ASK
    snapshot = policy.get_snapshot()
    assert snapshot["rules"]["read_file"]["source"] == "session"


def test_permission_policy_loads_modes_and_rules_from_trusted_settings():
    settings = build_trusted_settings_bundle(
        user_values={
            "permission": {
                "mode": "plan",
                "rules": {
                    "web_fetch": {"verdict": "allow", "reason": "research"},
                },
            }
        },
        project_values={
            "permission": {
                "rules": {
                    "write_file": {"verdict": "deny", "reason": "project locked"},
                }
            }
        },
    )

    policy = PermissionPolicy.from_trusted_settings(settings)

    assert policy.mode == PermissionMode.PLAN
    assert policy.mode_source == "user"
    assert policy.evaluate("web_fetch", "medium") == RuleVerdict.ALLOW
    assert policy.evaluate("write_file", "medium") == RuleVerdict.DENY

    snapshot = policy.get_snapshot()
    assert snapshot["policy_sources"]["user"]["mode"] == "plan"
    assert snapshot["policy_sources"]["project"]["rule_count"] == 1
    assert snapshot["mutation_policy"]["runtime_writable_sources"] == ["managed_policy", "session"]


def test_permission_policy_rejects_runtime_mutation_of_managed_sources():
    settings = build_trusted_settings_bundle()
    policy = PermissionPolicy.from_trusted_settings(settings)

    try:
        policy.add_rule("read_file", RuleVerdict.DENY, source="project")
    except PermissionError as exc:
        assert "not runtime-writable" in str(exc)
    else:
        raise AssertionError("expected managed source mutation to be rejected")
