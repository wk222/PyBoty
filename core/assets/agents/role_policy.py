"""Agent organizational role taxonomy and per-role policy defaults.

This module formalises the four structural roles an agent can take in a
team-based delegation hierarchy.  The existing ``AgentDefinition.role``
field continues to hold the agent's *domain persona* (e.g. "data analyst").
The new ``AgentDefinition.team_role`` field holds the *structural role* from
the enum below.

Role descriptions
-----------------
coordinator:
    Plans and delegates.  Issues sub-tasks to workers, aggregates results,
    and escalates to the human approval layer if any step requires it.
    Higher autonomy ceiling, lower micro-approval frequency.

worker:
    Executes a specific, bounded task delegated by a coordinator or by the
    root agent.  Strict tool-access scope; high-frequency micro-approvals for
    destructive operations.  Default role for all newly created agents.

verifier:
    Validates the output of a worker or coordinator without executing new
    side-effects itself.  Read-only tool access by default.  Returns a
    structured verdict (pass / fail / partial) rather than free-form output.

fork_child:
    Inherits the calling context from its parent (session history, memory
    projections, file views).  Used for parallelising work that shares
    context with the parent turn rather than starting from a blank slate.
    Lower overhead than spawning a fresh worker; higher coupling to parent.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class AgentRole(str, Enum):
    """Structural organizational role within a multi-agent team."""

    COORDINATOR = "coordinator"
    WORKER = "worker"
    VERIFIER = "verifier"
    FORK_CHILD = "fork_child"

    @classmethod
    def from_str(cls, value: str) -> "AgentRole":
        """Parse a string, falling back to WORKER for unknown values."""
        try:
            return cls(value.lower().strip())
        except ValueError:
            return cls.WORKER


@dataclass
class AgentRolePolicy:
    """Behavioral defaults applied when an agent is built for a given role.

    These are soft defaults — they can be overridden per-agent in
    ``AgentDefinition.capability_profile`` or ``middleware_profile``.
    """

    role: AgentRole

    autonomy_level: str = "reactive"
    """reactive | scheduled | autonomous"""

    max_tool_calls_per_turn: int | None = None
    """Hard cap on tool calls before returning to the coordinator.  None = no cap."""

    approval_threshold: str = "high"
    """low | medium | high | critical — minimum risk level that triggers approval."""

    allow_tool_creation: bool = False
    """Whether the agent may synthesize new tools at runtime."""

    allow_delegation: bool = False
    """Whether the agent may spawn sub-agents."""

    read_only_tools_only: bool = False
    """When True, only tools with risk <= MEDIUM are surfaced."""

    inherits_parent_context: bool = False
    """When True, the agent receives the parent session's compiled artifacts."""

    structured_output_required: bool = False
    """When True, the agent's final message must be parseable as JSON."""

    system_prompt_prefix: str = ""
    """Role-specific preamble prepended to the agent's system prompt."""

    extra_middleware: list[str] = field(default_factory=list)
    """Names of extra middleware components to activate for this role."""

    def to_dict(self) -> dict[str, Any]:
        return {
            "role": self.role.value,
            "autonomy_level": self.autonomy_level,
            "max_tool_calls_per_turn": self.max_tool_calls_per_turn,
            "approval_threshold": self.approval_threshold,
            "allow_tool_creation": self.allow_tool_creation,
            "allow_delegation": self.allow_delegation,
            "read_only_tools_only": self.read_only_tools_only,
            "inherits_parent_context": self.inherits_parent_context,
            "structured_output_required": self.structured_output_required,
            "system_prompt_prefix": self.system_prompt_prefix,
            "extra_middleware": self.extra_middleware,
        }


ROLE_POLICIES: dict[AgentRole, AgentRolePolicy] = {
    AgentRole.COORDINATOR: AgentRolePolicy(
        role=AgentRole.COORDINATOR,
        autonomy_level="autonomous",
        max_tool_calls_per_turn=None,
        approval_threshold="critical",
        allow_tool_creation=True,
        allow_delegation=True,
        read_only_tools_only=False,
        inherits_parent_context=False,
        structured_output_required=False,
        system_prompt_prefix=(
            "You are a coordinator agent. Your job is to plan and delegate, "
            "not to execute low-level tasks yourself. Break work into bounded "
            "sub-tasks, assign them to workers, and aggregate the results."
        ),
    ),
    AgentRole.WORKER: AgentRolePolicy(
        role=AgentRole.WORKER,
        autonomy_level="reactive",
        max_tool_calls_per_turn=20,
        approval_threshold="high",
        allow_tool_creation=False,
        allow_delegation=False,
        read_only_tools_only=False,
        inherits_parent_context=False,
        structured_output_required=False,
        system_prompt_prefix=(
            "You are a worker agent. Execute the specific task you have been "
            "assigned. Stay within the scope of your task and report results "
            "clearly when finished."
        ),
    ),
    AgentRole.VERIFIER: AgentRolePolicy(
        role=AgentRole.VERIFIER,
        autonomy_level="reactive",
        max_tool_calls_per_turn=10,
        approval_threshold="medium",
        allow_tool_creation=False,
        allow_delegation=False,
        read_only_tools_only=True,
        inherits_parent_context=False,
        structured_output_required=True,
        system_prompt_prefix=(
            "You are a verifier agent. Your role is to review and validate "
            "the output you have been given. Do not perform new actions or "
            "create side-effects. Return a structured verdict: "
            '{"verdict": "pass"|"fail"|"partial", "notes": "..."}.'
        ),
    ),
    AgentRole.FORK_CHILD: AgentRolePolicy(
        role=AgentRole.FORK_CHILD,
        autonomy_level="reactive",
        max_tool_calls_per_turn=15,
        approval_threshold="high",
        allow_tool_creation=False,
        allow_delegation=False,
        read_only_tools_only=False,
        inherits_parent_context=True,
        structured_output_required=False,
        system_prompt_prefix=(
            "You are a fork-child agent running in parallel with your siblings. "
            "You have access to the shared parent context. Complete your assigned "
            "slice of work and return a focused result."
        ),
    ),
}


def get_policy(role: AgentRole | str) -> AgentRolePolicy:
    """Return the policy for a given role.  Falls back to WORKER for unknowns."""
    if isinstance(role, str):
        role = AgentRole.from_str(role)
    return ROLE_POLICIES.get(role, ROLE_POLICIES[AgentRole.WORKER])


def apply_role_defaults(
    team_role: str,
    *,
    capability_profile: dict[str, Any],
    middleware_profile: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Merge role-policy defaults into agent profile dicts.

    Caller-supplied values in ``capability_profile`` / ``middleware_profile``
    always win over role defaults (non-destructive merge).

    Returns
    -------
    Updated (capability_profile, middleware_profile) tuple.
    """
    policy = get_policy(team_role)
    policy_dict = policy.to_dict()

    merged_cap = {
        "autonomy_level": policy_dict["autonomy_level"],
        "allow_tool_creation": policy_dict["allow_tool_creation"],
        "allow_delegation": policy_dict["allow_delegation"],
        "read_only_tools_only": policy_dict["read_only_tools_only"],
        "inherits_parent_context": policy_dict["inherits_parent_context"],
        **capability_profile,
    }

    merged_mid = {
        "approval_threshold": policy_dict["approval_threshold"],
        "max_tool_calls_per_turn": policy_dict["max_tool_calls_per_turn"],
        "structured_output_required": policy_dict["structured_output_required"],
        "extra_middleware": policy_dict["extra_middleware"],
        **middleware_profile,
    }

    return merged_cap, merged_mid
