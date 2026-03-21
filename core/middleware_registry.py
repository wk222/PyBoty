"""Self-describing middleware registry.

Each middleware registers a descriptor so that agent creators, the console UI,
and the ``AgentMiddlewareProfile`` preset system all share a single source of
truth about what middlewares exist, what they cost, and when to enable them.

Usage by parent agent or agent_creator::

    from core.middleware_registry import MIDDLEWARE_REGISTRY, query_middlewares

    # List all available enhancement middlewares
    for mw in query_middlewares(category="enhancement"):
        print(mw.section_key, mw.summary)

    # Check a specific middleware
    desc = MIDDLEWARE_REGISTRY["loop_guard"]
    print(desc.when_to_enable)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class MiddlewareDescriptor:
    """Immutable self-description of a middleware component."""

    section_key: str
    display_name: str
    summary: str
    category: str  # "core" | "enhancement" | "infrastructure"
    module_path: str
    class_name: str

    token_cost_estimate: str
    when_to_enable: str
    when_to_disable: str
    applies_to: tuple[str, ...]  # ("root", "subagent", "both")
    default_enabled_for_root: bool
    default_enabled_for_subagent: bool

    dependencies: tuple[str, ...] = ()
    conflicts_with: tuple[str, ...] = ()
    config_class: str | None = None
    tags: tuple[str, ...] = ()


MIDDLEWARE_REGISTRY: dict[str, MiddlewareDescriptor] = {}


def _register(desc: MiddlewareDescriptor) -> None:
    MIDDLEWARE_REGISTRY[desc.section_key] = desc


def query_middlewares(
    *,
    category: str | None = None,
    applies_to: str | None = None,
    tag: str | None = None,
) -> list[MiddlewareDescriptor]:
    """Filter registered middlewares by criteria."""
    results = list(MIDDLEWARE_REGISTRY.values())
    if category:
        results = [m for m in results if m.category == category]
    if applies_to:
        results = [m for m in results if applies_to in m.applies_to]
    if tag:
        results = [m for m in results if tag in m.tags]
    return results


def get_middleware_summary_for_agent() -> str:
    """Generate a human-readable summary for agent creation prompts."""
    lines = ["## Available Enhancement Middlewares", ""]
    for desc in query_middlewares(category="enhancement"):
        lines.append(f"### `{desc.section_key}` — {desc.display_name}")
        lines.append(f"  {desc.summary}")
        lines.append(f"  Token cost: {desc.token_cost_estimate}")
        lines.append(f"  Enable when: {desc.when_to_enable}")
        lines.append(f"  Disable when: {desc.when_to_disable}")
        if desc.dependencies:
            lines.append(f"  Requires: {', '.join(desc.dependencies)}")
        lines.append("")
    return "\n".join(lines)


def list_section_keys() -> list[str]:
    """All registered section keys for validation."""
    return list(MIDDLEWARE_REGISTRY.keys())


# ── Core middlewares (always present in root, not optional) ─────────────────

_register(MiddlewareDescriptor(
    section_key="prompt_context",
    display_name="Prompt Context",
    summary="Injects workspace context, memory, and active skill extensions into the system prompt.",
    category="core",
    module_path=".agent_prompt_middleware",
    class_name="PromptSectionMiddleware",
    token_cost_estimate="~200-500 tokens (varies with workspace size)",
    when_to_enable="Always — provides essential runtime context.",
    when_to_disable="Never for root agent. Subagents may omit if tightly scoped.",
    applies_to=("root", "subagent"),
    default_enabled_for_root=True,
    default_enabled_for_subagent=True,
))

_register(MiddlewareDescriptor(
    section_key="policy_context",
    display_name="Policy Context",
    summary="Injects governance policy (blocked tools, approval rules, sandbox mode) into the prompt.",
    category="core",
    module_path=".agent_prompt_middleware",
    class_name="PromptSectionMiddleware",
    token_cost_estimate="~100-200 tokens",
    when_to_enable="When agent has governance constraints or tool restrictions.",
    when_to_disable="Focused specialists with no restrictions.",
    applies_to=("subagent",),
    default_enabled_for_root=False,
    default_enabled_for_subagent=True,
))

_register(MiddlewareDescriptor(
    section_key="tool_control",
    display_name="Tool Control",
    summary="Enforces tool access policy, approval workflows, and dynamic tool injection. Required.",
    category="infrastructure",
    module_path=".tool_middleware",
    class_name="DynamicToolMiddleware",
    token_cost_estimate="~0 tokens (runtime enforcement only)",
    when_to_enable="Always — this is mandatory infrastructure.",
    when_to_disable="Never.",
    applies_to=("root", "subagent"),
    default_enabled_for_root=True,
    default_enabled_for_subagent=True,
))

_register(MiddlewareDescriptor(
    section_key="delegation_context",
    display_name="Delegation Context",
    summary="Describes delegation rules and constraints for coordinator-style agents.",
    category="core",
    module_path=".agent_middleware_factory",
    class_name="PromptSectionMiddleware",
    token_cost_estimate="~100 tokens",
    when_to_enable="Coordinator agents that can create/delegate to sub-agents.",
    when_to_disable="Leaf agents that don't delegate.",
    applies_to=("subagent",),
    default_enabled_for_root=False,
    default_enabled_for_subagent=False,
))

_register(MiddlewareDescriptor(
    section_key="execution_context",
    display_name="Execution Context",
    summary="Describes sandbox boundaries and code execution permissions for builder agents.",
    category="core",
    module_path=".agent_middleware_factory",
    class_name="PromptSectionMiddleware",
    token_cost_estimate="~80 tokens",
    when_to_enable="Builder agents with code execution or sandbox writes.",
    when_to_disable="Read-only or coordinator agents.",
    applies_to=("subagent",),
    default_enabled_for_root=False,
    default_enabled_for_subagent=False,
))

# ── Enhancement middlewares (optional, togglable per-agent) ─────────────────

_register(MiddlewareDescriptor(
    section_key="loop_guard",
    display_name="LoopGuard",
    summary="Detects repeated identical tool calls (behavioral loops) and injects correction "
            "hints. If loops persist, flags the agent for model escalation. Directly saves "
            "tokens by breaking wasteful loops early.",
    category="enhancement",
    module_path=".loop_guard_middleware",
    class_name="LoopGuardMiddleware",
    config_class="LoopGuardConfig",
    token_cost_estimate="~150 tokens (only when loop detected, otherwise 0)",
    when_to_enable="Complex tasks, autonomous agents, long-running sessions, or agents that "
                   "use tools heavily. Especially important for builder/coder agents.",
    when_to_disable="Simple Q&A agents, or very short-lived agents (< 3 tool calls).",
    applies_to=("root", "subagent"),
    default_enabled_for_root=True,
    default_enabled_for_subagent=False,
    tags=("token_saving", "reliability", "auto_recovery"),
))

_register(MiddlewareDescriptor(
    section_key="insight_vault",
    display_name="InsightVault",
    summary="Task-level experience pool. Stores LLM-distilled traces of successful task "
            "completions in a vector store. On new tasks, retrieves similar past experiences "
            "as few-shot examples. Agents get smarter over time.",
    category="enhancement",
    module_path=".insight_vault_middleware",
    class_name="InsightVaultMiddleware",
    config_class="InsightVaultConfig",
    token_cost_estimate="~200-400 tokens when experiences found (0 if no matches)",
    when_to_enable="Agents that handle recurring task patterns — data analysis, code generation, "
                   "report writing. Most valuable for long-lived root agents.",
    when_to_disable="One-off tasks, subagents with unique roles, or when vector store is unavailable.",
    applies_to=("root", "subagent"),
    default_enabled_for_root=True,
    default_enabled_for_subagent=False,
    dependencies=("vector_store",),
    tags=("learning", "few_shot", "token_saving"),
))

_register(MiddlewareDescriptor(
    section_key="reasoning_frame",
    display_name="ReasoningFrame",
    summary="Injects a structured thinking scaffold (Observation → Analysis → Self-Critique → "
            "Plan → Action Rationale) into the system prompt. Activates automatically on complex "
            "tasks. Reduces false starts and improves multi-step decision quality.",
    category="enhancement",
    module_path=".reasoning_frame_middleware",
    class_name="ReasoningFrameMiddleware",
    config_class="ReasoningFrameConfig",
    token_cost_estimate="~200 tokens (constant once activated)",
    when_to_enable="Complex multi-step tasks: debugging, refactoring, architecture design, "
                   "migration. Agents that make tool-selection decisions.",
    when_to_disable="Simple retrieval agents, chat-only agents, or when strict mode wastes "
                    "output tokens on reasoning blocks.",
    applies_to=("root", "subagent"),
    default_enabled_for_root=True,
    default_enabled_for_subagent=False,
    tags=("decision_quality", "transparency", "debugging"),
))

_register(MiddlewareDescriptor(
    section_key="tool_arg_repair",
    display_name="Tool Argument Repair",
    summary="Auto-repairs LLM tool arguments before Pydantic validation — fixes type mismatches and broken JS code.",
    category="infrastructure",
    module_path=".tool_arg_repair_middleware",
    class_name="ToolArgRepairMiddleware",
    token_cost_estimate="~0 tokens (runtime repair only)",
    when_to_enable="Always — prevents ValidationError from LLM type mismatches and JS regex damage.",
    when_to_disable="Only if custom pre-validation logic is used instead.",
    applies_to=("root", "subagent"),
    default_enabled_for_root=True,
    default_enabled_for_subagent=True,
    tags=("reliability", "tool_quality", "auto_repair"),
))
