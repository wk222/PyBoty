"""Translate subagent capability profiles into enforceable control policy.

Also provides multi-level delegation chain visualization and
governance reporting for admin UI.
"""

from __future__ import annotations

from typing import Any

from core.systems.governance.subagent_sandbox import resolve_sandbox_adapter
from core.systems.governance.agent_control import (
    AGENT_DELEGATION_TOOLS,
    AGENT_MUTATION_TOOLS,
    TOOL_MUTATION_TOOLS,
    AgentControlPolicy,
)

from .agent_capability_profile import AgentCapabilityProfile
from .agent_middleware_profile import AgentMiddlewareProfile

CODE_EXECUTION_TOOLS = frozenset({"exec_code", "iterative_test", "run_tests"})
WORKFLOW_MANAGEMENT_TOOLS = frozenset({"run_workflow", "resume_workflow", "generate_workflow", "trigger_workflow"})
SKILL_ADMIN_TOOLS = frozenset({"install_skill", "uninstall_skill", "create_skill", "package_skill"})
APP_MUTATION_TOOLS = frozenset({"create_app", "update_app_file", "delete_app"})

SANDBOX_SENSITIVE_TOOLS = (
    CODE_EXECUTION_TOOLS
    | WORKFLOW_MANAGEMENT_TOOLS
    | SKILL_ADMIN_TOOLS
    | APP_MUTATION_TOOLS
    | TOOL_MUTATION_TOOLS
    | AGENT_MUTATION_TOOLS
    | AGENT_DELEGATION_TOOLS
)
READ_ONLY_BLOCKED_TOOLS = SANDBOX_SENSITIVE_TOOLS | frozenset({"verify_app"})


def derive_subagent_control_policy(
    *,
    base_policy: AgentControlPolicy | None,
    capability_profile: AgentCapabilityProfile,
) -> AgentControlPolicy:
    policy = base_policy or AgentControlPolicy()
    mode = policy.mode if capability_profile.control_mode == "inherit" else capability_profile.control_mode

    blocked_tools = set(policy.blocked_tools)
    approval_required_tools = set(policy.approval_required_tools)

    blocked_tools.update(capability_profile.blocked_tools)
    approval_required_tools.update(capability_profile.approval_required_tools)

    if not capability_profile.allow_code_execution:
        blocked_tools.update(CODE_EXECUTION_TOOLS)

    if not capability_profile.allow_workflow_management:
        blocked_tools.update(WORKFLOW_MANAGEMENT_TOOLS)

    if not capability_profile.allow_skill_installation:
        blocked_tools.update(SKILL_ADMIN_TOOLS)

    if not capability_profile.allow_app_mutation:
        blocked_tools.update(APP_MUTATION_TOOLS)

    if capability_profile.sandbox_mode == "restricted":
        approval_required_tools.update(SANDBOX_SENSITIVE_TOOLS)
    elif capability_profile.sandbox_mode == "read_only":
        blocked_tools.update(READ_ONLY_BLOCKED_TOOLS)

    return AgentControlPolicy(
        mode=mode,
        blocked_tools=frozenset(sorted(blocked_tools)),
        blocked_dynamic_tools=policy.blocked_dynamic_tools,
        risky_tools=policy.risky_tools,
        approval_required_tools=frozenset(sorted(approval_required_tools)),
        approval_required_dynamic_tools=policy.approval_required_dynamic_tools,
        allow_dynamic_tools=policy.allow_dynamic_tools and capability_profile.allow_local_dynamic_tools,
        allow_tool_mutation=policy.allow_tool_mutation
        and (
            capability_profile.allow_local_tool_creation
            or capability_profile.allow_local_tool_removal
            or capability_profile.allow_template_tools
        ),
        allow_agent_mutation=policy.allow_agent_mutation
        and (capability_profile.allow_agent_creation or capability_profile.allow_agent_removal),
        allow_agent_delegation=policy.allow_agent_delegation and capability_profile.allow_agent_delegation,
        max_subagent_depth=policy.max_subagent_depth,
        max_concurrent_subagents=policy.max_concurrent_subagents,
        subagent_timeout_seconds=policy.subagent_timeout_seconds,
        max_recent_tool_calls=policy.max_recent_tool_calls,
        stuck_loop_warning_threshold=policy.stuck_loop_warning_threshold,
        stuck_loop_kill_threshold=policy.stuck_loop_kill_threshold,
    )


def filter_tools_for_policy(
    *,
    tools: list[Any],
    control_policy: AgentControlPolicy,
    dynamic_tool_names: set[str] | None = None,
) -> list[Any]:
    dynamic_names = dynamic_tool_names or set()
    filtered: list[Any] = []
    seen_names: set[str] = set()
    for tool in tools:
        tool_name = getattr(tool, "name", "")
        if not tool_name or tool_name in seen_names:
            continue
        decision = control_policy.evaluate_tool_call(tool_name, is_dynamic=tool_name in dynamic_names)
        if decision.allowed:
            filtered.append(tool)
            seen_names.add(tool_name)
    return filtered


def build_subagent_governance_snapshot(
    *,
    base_policy: AgentControlPolicy | None,
    capability_profile: AgentCapabilityProfile,
    middleware_profile: AgentMiddlewareProfile,
) -> dict[str, Any]:
    """Return a UI-friendly governance summary for a subagent."""
    inherited_policy = base_policy or AgentControlPolicy()
    effective_policy = derive_subagent_control_policy(
        base_policy=inherited_policy,
        capability_profile=capability_profile,
    )
    inherited_blocked = set(inherited_policy.blocked_tools)
    inherited_approval = set(inherited_policy.approval_required_tools)
    effective_blocked = set(effective_policy.blocked_tools)
    effective_approval = set(effective_policy.approval_required_tools)
    effective_sandbox_adapter = resolve_sandbox_adapter(capability_profile)

    return {
        "root_policy": inherited_policy.to_dict(),
        "effective_policy": effective_policy.to_dict(),
        "delegated_base_policy": effective_policy.to_dict(),
        "capability_profile": capability_profile.to_dict(),
        "middleware_profile": middleware_profile.to_dict(),
        "middleware_stack": middleware_profile.stack_names(),
        "inheritance": {
            "root_mode": inherited_policy.mode,
            "requested_control_mode": capability_profile.control_mode,
            "effective_control_mode": effective_policy.mode,
            "sandbox_mode": capability_profile.sandbox_mode,
            "requested_sandbox_adapter": capability_profile.sandbox_adapter,
            "effective_sandbox_adapter": effective_sandbox_adapter,
            "blocked_tools_added": sorted(effective_blocked - inherited_blocked),
            "approval_tools_added": sorted(effective_approval - inherited_approval),
            "delegation_continues_with_effective_policy": True,
        },
        "sandbox": {
            "mode": capability_profile.sandbox_mode,
            "adapter": effective_sandbox_adapter,
            "backend": "local",
        },
        "permissions": {
            "allow_local_tool_creation": capability_profile.allow_local_tool_creation,
            "allow_agent_delegation": capability_profile.allow_agent_delegation,
            "allow_code_execution": capability_profile.allow_code_execution,
            "allow_workflow_management": capability_profile.allow_workflow_management,
            "allow_skill_installation": capability_profile.allow_skill_installation,
            "allow_app_mutation": capability_profile.allow_app_mutation,
        },
        "lifecycle_limits": {
            "max_subagent_depth": effective_policy.max_subagent_depth,
            "max_concurrent_subagents": effective_policy.max_concurrent_subagents,
            "subagent_timeout_seconds": effective_policy.subagent_timeout_seconds,
        },
    }


def build_delegation_chain(
    agents: list[dict[str, Any]],
    root_policy: AgentControlPolicy | None = None,
) -> list[dict[str, Any]]:
    """Build a multi-level delegation chain showing policy inheritance.

    Each entry in ``agents`` should have:
    - ``name``: agent name
    - ``role``: agent role
    - ``capability_profile``: an ``AgentCapabilityProfile`` instance
    - ``parent``: parent agent name (None for root)

    Returns a list of chain nodes with cumulative policy diffs.
    """
    policy = root_policy or AgentControlPolicy()
    chain: list[dict[str, Any]] = [
        {
            "name": "root",
            "role": "root",
            "level": 0,
            "policy": policy.to_dict(),
            "blocked_tools": sorted(policy.blocked_tools),
            "approval_tools": sorted(policy.approval_required_tools),
            "sandbox": "n/a",
            "new_restrictions": [],
        }
    ]

    name_to_agent = {a["name"]: a for a in agents}
    children_map: dict[str | None, list[str]] = {}
    for agent in agents:
        parent = agent.get("parent")
        children_map.setdefault(parent, []).append(agent["name"])

    def _walk(parent_name: str | None, parent_policy: AgentControlPolicy, level: int) -> None:
        for child_name in children_map.get(parent_name, []):
            agent = name_to_agent[child_name]
            cap = agent["capability_profile"]
            effective = derive_subagent_control_policy(
                base_policy=parent_policy,
                capability_profile=cap,
            )
            parent_blocked = set(parent_policy.blocked_tools)
            new_blocked = sorted(set(effective.blocked_tools) - parent_blocked)
            parent_approval = set(parent_policy.approval_required_tools)
            new_approval = sorted(set(effective.approval_required_tools) - parent_approval)

            chain.append(
                {
                    "name": child_name,
                    "role": agent.get("role", ""),
                    "level": level,
                    "policy": effective.to_dict(),
                    "blocked_tools": sorted(effective.blocked_tools),
                    "approval_tools": sorted(effective.approval_required_tools),
                    "sandbox": resolve_sandbox_adapter(cap),
                    "new_restrictions": new_blocked + [f"approval:{t}" for t in new_approval],
                }
            )
            _walk(child_name, effective, level + 1)

    _walk(None, policy, 1)
    return chain


def format_delegation_tree(chain: list[dict[str, Any]]) -> str:
    """Render a delegation chain as an ASCII tree for admin display."""
    lines: list[str] = []
    for node in chain:
        indent = "  " * node["level"]
        prefix = "|-- " if node["level"] > 0 else ""
        restrictions = ""
        if node["new_restrictions"]:
            restrictions = f" [+{len(node['new_restrictions'])} restrictions]"
        lines.append(f"{indent}{prefix}{node['name']} ({node['role']}) sandbox={node['sandbox']}{restrictions}")
    return "\n".join(lines)
