"""Tests for agent role taxonomy and role-policy defaults."""

from __future__ import annotations

import pytest

from core.modes.agents.agent_role_policy import (
    AgentRole,
    AgentRolePolicy,
    ROLE_POLICIES,
    apply_role_defaults,
    get_policy,
)
from core.modes.agents.agent_storage import AgentDefinition
from core.modes.agents.agent_tool_inventory import build_effective_profiles


# ---------------------------------------------------------------------------
# AgentRole enum
# ---------------------------------------------------------------------------

class TestAgentRole:
    def test_all_variants_exist(self):
        assert AgentRole.COORDINATOR
        assert AgentRole.WORKER
        assert AgentRole.VERIFIER
        assert AgentRole.FORK_CHILD

    def test_str_values(self):
        assert AgentRole.COORDINATOR.value == "coordinator"
        assert AgentRole.WORKER.value == "worker"
        assert AgentRole.VERIFIER.value == "verifier"
        assert AgentRole.FORK_CHILD.value == "fork_child"

    def test_from_str_valid(self):
        assert AgentRole.from_str("coordinator") == AgentRole.COORDINATOR
        assert AgentRole.from_str("WORKER") == AgentRole.WORKER
        assert AgentRole.from_str("verifier") == AgentRole.VERIFIER

    def test_from_str_unknown_falls_back_to_worker(self):
        assert AgentRole.from_str("unknown_role") == AgentRole.WORKER
        assert AgentRole.from_str("") == AgentRole.WORKER

    def test_from_str_case_insensitive(self):
        assert AgentRole.from_str("FORK_CHILD") == AgentRole.FORK_CHILD


# ---------------------------------------------------------------------------
# Role policy registry completeness
# ---------------------------------------------------------------------------

class TestRolePoliciesRegistry:
    def test_all_roles_have_policies(self):
        for role in AgentRole:
            assert role in ROLE_POLICIES, f"Missing policy for {role}"

    def test_policy_role_matches_key(self):
        for role, policy in ROLE_POLICIES.items():
            assert policy.role == role

    def test_to_dict_round_trips_role(self):
        for role, policy in ROLE_POLICIES.items():
            d = policy.to_dict()
            assert d["role"] == role.value


# ---------------------------------------------------------------------------
# Individual role policy assertions
# ---------------------------------------------------------------------------

class TestCoordinatorPolicy:
    def setup_method(self):
        self.policy = ROLE_POLICIES[AgentRole.COORDINATOR]

    def test_autonomous(self):
        assert self.policy.autonomy_level == "autonomous"

    def test_can_create_tools(self):
        assert self.policy.allow_tool_creation is True

    def test_can_delegate(self):
        assert self.policy.allow_delegation is True

    def test_not_read_only(self):
        assert self.policy.read_only_tools_only is False

    def test_no_structured_output_required(self):
        assert self.policy.structured_output_required is False


class TestWorkerPolicy:
    def setup_method(self):
        self.policy = ROLE_POLICIES[AgentRole.WORKER]

    def test_reactive(self):
        assert self.policy.autonomy_level == "reactive"

    def test_cannot_create_tools(self):
        assert self.policy.allow_tool_creation is False

    def test_cannot_delegate(self):
        assert self.policy.allow_delegation is False

    def test_capped_tool_calls(self):
        assert self.policy.max_tool_calls_per_turn is not None
        assert self.policy.max_tool_calls_per_turn > 0


class TestVerifierPolicy:
    def setup_method(self):
        self.policy = ROLE_POLICIES[AgentRole.VERIFIER]

    def test_read_only(self):
        assert self.policy.read_only_tools_only is True

    def test_structured_output_required(self):
        assert self.policy.structured_output_required is True

    def test_cannot_delegate(self):
        assert self.policy.allow_delegation is False


class TestForkChildPolicy:
    def setup_method(self):
        self.policy = ROLE_POLICIES[AgentRole.FORK_CHILD]

    def test_inherits_parent_context(self):
        assert self.policy.inherits_parent_context is True

    def test_cannot_delegate(self):
        assert self.policy.allow_delegation is False

    def test_capped_tool_calls(self):
        assert self.policy.max_tool_calls_per_turn is not None


# ---------------------------------------------------------------------------
# get_policy
# ---------------------------------------------------------------------------

class TestGetPolicy:
    def test_get_by_enum(self):
        p = get_policy(AgentRole.COORDINATOR)
        assert p.role == AgentRole.COORDINATOR

    def test_get_by_string(self):
        p = get_policy("worker")
        assert p.role == AgentRole.WORKER

    def test_unknown_string_returns_worker(self):
        p = get_policy("nonexistent")
        assert p.role == AgentRole.WORKER


# ---------------------------------------------------------------------------
# apply_role_defaults â€?merging behavior
# ---------------------------------------------------------------------------

class TestApplyRoleDefaults:
    def test_role_defaults_applied_when_profile_empty(self):
        cap, mid = apply_role_defaults("coordinator", capability_profile={}, middleware_profile={})
        assert cap["allow_tool_creation"] is True
        assert cap["allow_delegation"] is True
        assert cap["autonomy_level"] == "autonomous"
        assert mid["approval_threshold"] == "critical"

    def test_caller_overrides_role_defaults(self):
        cap, mid = apply_role_defaults(
            "coordinator",
            capability_profile={"allow_tool_creation": False},
            middleware_profile={"approval_threshold": "low"},
        )
        assert cap["allow_tool_creation"] is False
        assert mid["approval_threshold"] == "low"

    def test_worker_defaults(self):
        cap, mid = apply_role_defaults("worker", capability_profile={}, middleware_profile={})
        assert cap["allow_delegation"] is False
        assert mid["structured_output_required"] is False

    def test_verifier_defaults(self):
        cap, mid = apply_role_defaults("verifier", capability_profile={}, middleware_profile={})
        assert cap["read_only_tools_only"] is True
        assert mid["structured_output_required"] is True

    def test_fork_child_inherits_parent(self):
        cap, _ = apply_role_defaults("fork_child", capability_profile={}, middleware_profile={})
        assert cap["inherits_parent_context"] is True

    def test_unknown_role_uses_worker(self):
        cap, mid = apply_role_defaults("mystery_role", capability_profile={}, middleware_profile={})
        assert cap["allow_delegation"] is False


# ---------------------------------------------------------------------------
# AgentDefinition.team_role
# ---------------------------------------------------------------------------

class TestAgentDefinitionTeamRole:
    def _make_agent(self, team_role: str = "worker") -> AgentDefinition:
        return AgentDefinition(
            name="test_agent",
            role="data analyst",
            description="an agent",
            system_prompt="you are helpful",
            team_role=team_role,
        )

    def test_default_team_role_is_worker(self):
        agent = AgentDefinition(
            name="a", role="r", description="d", system_prompt="s"
        )
        assert agent.team_role == "worker"

    def test_team_role_stored_in_to_dict(self):
        agent = self._make_agent("coordinator")
        d = agent.to_dict()
        assert d["team_role"] == "coordinator"

    def test_team_role_roundtrips_through_from_dict(self):
        agent = self._make_agent("verifier")
        d = agent.to_dict()
        restored = AgentDefinition.from_dict(d)
        assert restored.team_role == "verifier"

    def test_from_dict_missing_team_role_defaults_to_worker(self):
        d = {
            "name": "a", "role": "r", "description": "d",
            "system_prompt": "s",
        }
        restored = AgentDefinition.from_dict(d)
        assert restored.team_role == "worker"


# ---------------------------------------------------------------------------
# build_effective_profiles
# ---------------------------------------------------------------------------

class TestBuildEffectiveProfiles:
    def test_coordinator_effective_profiles(self):
        agent = AgentDefinition(
            name="coord", role="planner", description="d",
            system_prompt="s", team_role="coordinator"
        )
        cap, mid = build_effective_profiles(agent)
        assert cap["allow_tool_creation"] is True
        assert cap["allow_delegation"] is True

    def test_agent_overrides_win(self):
        agent = AgentDefinition(
            name="w", role="r", description="d", system_prompt="s",
            team_role="coordinator",
            capability_profile={"allow_tool_creation": False},
            middleware_profile={"approval_threshold": "low"},
        )
        cap, mid = build_effective_profiles(agent)
        assert cap["allow_tool_creation"] is False
        assert mid["approval_threshold"] == "low"

    def test_verifier_profiles(self):
        agent = AgentDefinition(
            name="v", role="checker", description="d",
            system_prompt="s", team_role="verifier"
        )
        cap, mid = build_effective_profiles(agent)
        assert cap["read_only_tools_only"] is True
        assert mid["structured_output_required"] is True

