"""Capability tree taxonomy and projection helpers."""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from core.systems.runtime.projected_runtime_view import coerce_projected_runtime_view

from .capability_bus_models import Capability, CapabilityLayer

_TRUNK_NODE_IDS = [
    "tool_runtime_governance",
    "workspace_view",
    "context_hygiene",
    "session_continuity",
    "permission_recovery",
]

_TREE_TEMPLATE: dict[str, Any] = {
    "root": "PyBot",
    "trunk": [
        {
            "id": "tool_runtime_governance",
            "label": "Tool Runtime / Governance",
            "description": "Execution base for tools, approvals, and safety policy.",
            "depends_on": [],
        },
        {
            "id": "workspace_view",
            "label": "Workspace View",
            "description": "Shared file-view state, read dedupe, and workspace projections.",
            "depends_on": ["tool_runtime_governance"],
        },
        {
            "id": "context_hygiene",
            "label": "Context Hygiene",
            "description": "Compaction, summarization, and prompt-shaping that keep the session usable.",
            "depends_on": ["tool_runtime_governance", "workspace_view"],
        },
        {
            "id": "session_continuity",
            "label": "Session Continuity",
            "description": "Session notes, artifacts, todos, and resume bundles that preserve momentum.",
            "depends_on": ["workspace_view", "context_hygiene"],
        },
        {
            "id": "permission_recovery",
            "label": "Permission / Recovery",
            "description": "Mode/rule control plane, approvals, and crash-safe recovery state.",
            "depends_on": ["tool_runtime_governance", "session_continuity"],
        },
    ],
    "execution_surfaces": [
        {
            "id": "single_agent_runtime",
            "label": "Single-Agent Runtime",
            "description": "The base agent loop that turns the trunk into a usable assistant.",
            "depends_on": list(_TRUNK_NODE_IDS),
        },
        {
            "id": "skill_strategy",
            "label": "Skill Strategy Overlay",
            "description": "Prompt strategy and optional skill tools layered on top of the single-agent runtime.",
            "depends_on": ["single_agent_runtime"],
        },
    ],
    "primary_branches": [
        {
            "id": "web",
            "label": "Web / External Info",
            "description": "Fetch, search, and external-information access built on the trunk.",
            "depends_on": ["tool_runtime_governance", "context_hygiene", "permission_recovery"],
        },
        {
            "id": "knowledge_rag",
            "label": "Knowledge / RAG",
            "description": "Retrieval, semantic memory, and knowledge grounding.",
            "depends_on": ["workspace_view", "session_continuity", "single_agent_runtime"],
        },
        {
            "id": "workflow_apps",
            "label": "Workflow / Apps / Automation",
            "description": "Workflow execution, app runtime, and higher-level automation products.",
            "depends_on": ["single_agent_runtime", "tool_runtime_governance", "permission_recovery"],
            "children": [
                {
                    "id": "workflow_runtime",
                    "label": "Workflow Runtime",
                    "description": "Core workflow execution and lifecycle capabilities.",
                    "depends_on": ["single_agent_runtime", "tool_runtime_governance"],
                },
                {
                    "id": "workflow_collaboration",
                    "label": "Workflow Collaboration",
                    "description": "Workflow nodes that depend on delegated agents and approvals.",
                    "depends_on": ["workflow_runtime", "multi_agent", "permission_recovery"],
                },
                {
                    "id": "app_runtime",
                    "label": "App Asset Runtime",
                    "description": "App creation, verification, packaging, and file-safe mutation.",
                    "depends_on": ["tool_runtime_governance", "permission_recovery"],
                },
                {
                    "id": "app_modes",
                    "label": "App Modes",
                    "description": "Assistant/chat/rag/workflow app modes that reuse other branches.",
                    "depends_on": ["app_runtime", "single_agent_runtime"],
                },
                {
                    "id": "app_orchestration",
                    "label": "App Orchestration",
                    "description": "App topology, matrix routing, and higher-order composition.",
                    "depends_on": ["app_runtime", "workflow_runtime", "multi_agent"],
                },
            ],
        },
        {
            "id": "multi_agent",
            "label": "Multi-Agent Organization",
            "description": "Delegation, subagent runtime, and team-level coordination.",
            "depends_on": ["single_agent_runtime", "session_continuity", "permission_recovery", "isolation_model"],
            "children": [
                {
                    "id": "isolation_model",
                    "label": "Isolation Model",
                    "description": "Sandbox, worktree, and visibility contracts required before delegation.",
                    "depends_on": ["tool_runtime_governance", "workspace_view", "permission_recovery"],
                },
                {
                    "id": "subagent_runtime",
                    "label": "Subagent Runtime",
                    "description": "Reusable delegated-agent execution on top of the single-agent spine.",
                    "depends_on": ["single_agent_runtime", "session_continuity", "permission_recovery", "isolation_model"],
                }
            ],
        },
    ],
    "secondary_branches": [
        {
            "id": "hooks_runtime",
            "label": "Hooks Runtime",
            "description": "Runtime hooks and extension points around the agent loop.",
            "depends_on": ["tool_runtime_governance", "context_hygiene"],
        },
        {
            "id": "plugins_marketplace",
            "label": "Plugins / Marketplace",
            "description": "Packaging, distribution, and discovery layers on top of core branches.",
            "depends_on": ["skill_strategy", "workflow_apps", "multi_agent"],
        },
        {
            "id": "projected_view_history_snip",
            "label": "Projected View / History Snip",
            "description": "Projected runtime views and split UI/runtime history windows.",
            "depends_on": ["workspace_view", "context_hygiene", "session_continuity"],
        },
        {
            "id": "ui_overlay",
            "label": "UI Overlay / Review Surface",
            "description": "Presentation-only overlays for apps, review, and operator-facing surfaces.",
            "depends_on": ["workflow_apps", "web", "single_agent_runtime"],
        },
    ],
}

_NODE_INDEX: dict[str, dict[str, Any]] = {}
for section_name in ("trunk", "execution_surfaces", "primary_branches", "secondary_branches"):
    for node in _TREE_TEMPLATE[section_name]:
        _NODE_INDEX[node["id"]] = node
        for child in node.get("children", []):
            _NODE_INDEX[child["id"]] = child

_TOOL_RUNTIME_TOOL_NAMES = {
    "bash",
    "write_file",
    "str_replace",
    "capability_bus",
    "capability_registry",
}
_WORKSPACE_VIEW_TOOL_NAMES = {
    "read_file",
    "grep_files",
    "glob_files",
    "list_directory",
    "read_app_file",
}
_APP_RUNTIME_TOOL_NAMES = {
    "create_app",
    "update_app_file",
    "verify_app",
    "test_app_api",
    "build_app_iteratively",
    "package_app",
}
_PERMISSION_TOOL_TOKENS = ("permission", "approval", "governance")
_SESSION_TOOL_TOKENS = ("session", "resume", "artifact", "todo", "memory", "notebook")
_CONTEXT_TOOL_TOKENS = ("compact", "context", "summary", "summar", "history", "projection", "snip")
_WEB_TOKENS = ("web", "fetch", "browser", "duckduckgo", "search", "url")
_KNOWLEDGE_TOKENS = ("knowledge", "rag", "retriev", "vector", "semantic", "insight", "vault")
_MULTI_AGENT_TOKENS = ("delegate", "subagent", "swarm", "coordinator", "mailbox", "teammate")
_WORKFLOW_TOKENS = ("workflow", "pyflow", "scheduler", "automation")
_APP_TOKENS = ("app", "app_", "_app")
_PLUGIN_TOKENS = ("plugin", "marketplace")
_HOOK_TOKENS = ("hook",)
_ISOLATION_TOKENS = ("isolation", "sandbox", "worktree", "cwd", "visibility")
_UI_TOKENS = ("ui", "overlay", "review", "comment")
_TIER_SELECTION_PRIORITY = {
    "trunk": 0,
    "execution_surface": 1,
    "primary_branch": 2,
    "secondary_branch": 3,
}
_SLOT_SELECTION_PRIORITY = {
    "tool_runtime_governance": 0,
    "workspace_view": 1,
    "context_hygiene": 2,
    "session_continuity": 3,
    "permission_recovery": 4,
    "single_agent_runtime": 5,
    "skill_strategy": 6,
    "workflow_runtime": 7,
    "workflow_collaboration": 8,
    "app_runtime": 9,
    "app_modes": 10,
    "app_orchestration": 11,
    "knowledge_rag": 12,
    "web": 13,
    "isolation_model": 14,
    "subagent_runtime": 15,
    "multi_agent": 16,
    "hooks_runtime": 17,
    "plugins_marketplace": 18,
    "projected_view_history_snip": 19,
    "ui_overlay": 20,
}


def annotate_capability_tree_metadata(
    *,
    name: str,
    layer: CapabilityLayer,
    description: str = "",
    tags: list[str] | None = None,
    dependencies: list[str] | None = None,
    provides: list[str] | None = None,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Return metadata annotated with inferred capability-tree placement."""

    merged_metadata = dict(metadata or {})
    existing_tree = dict(merged_metadata.get("tree") or {})
    inferred_tree = infer_capability_tree(
        name=name,
        layer=layer,
        description=description,
        tags=tags,
        dependencies=dependencies,
        provides=provides,
        metadata=merged_metadata,
    )
    inferred_tree.update({key: value for key, value in existing_tree.items() if value not in (None, "", [], {})})
    merged_metadata["tree"] = inferred_tree
    return merged_metadata


def infer_capability_tree(
    *,
    name: str,
    layer: CapabilityLayer,
    description: str = "",
    tags: list[str] | None = None,
    dependencies: list[str] | None = None,
    provides: list[str] | None = None,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Infer tree placement for one capability."""

    merged_metadata = dict(metadata or {})
    existing_tree = dict(merged_metadata.get("tree") or {})
    if existing_tree.get("slot"):
        return _finalize_tree(existing_tree)

    tag_values = [str(tag) for tag in tags or []]
    dependency_values = _unique_strings(dependencies or [])
    provide_values = _unique_strings(provides or [])
    text = _text_blob(
        name,
        description,
        tag_values,
        dependency_values,
        provide_values,
        list(merged_metadata.keys()),
        list(merged_metadata.values()),
    )

    if layer == CapabilityLayer.TOOL:
        tree = _infer_tool_tree(name, text)
    elif layer == CapabilityLayer.SKILL:
        tree = {
            "tier": "execution_surface",
            "slot": "skill_strategy",
            "top_level": "skill_strategy",
            "role": "strategy_skill",
            "depends_on_slots": ["single_agent_runtime"],
            "supports": _infer_supported_branches(text),
        }
    elif layer == CapabilityLayer.AGENT:
        tree = {
            "tier": "primary_branch",
            "slot": "subagent_runtime",
            "top_level": "multi_agent",
            "role": f"{str(merged_metadata.get('team_role', 'worker')).strip() or 'worker'}_agent",
            "depends_on_slots": _unique_strings(
                ["single_agent_runtime", "session_continuity", "permission_recovery", "isolation_model"]
                + (["workflow_runtime"] if dependency_values else [])
            ),
        }
    elif layer == CapabilityLayer.WORKFLOW:
        collaborative = _contains_any(text, ("agent", "debate", "consensus", "supervisor", "collaboration"))
        tree = {
            "tier": "primary_branch",
            "slot": "workflow_collaboration" if collaborative else "workflow_runtime",
            "top_level": "workflow_apps",
            "role": "collaborative_workflow" if collaborative else "workflow_runtime",
            "depends_on_slots": _unique_strings(
                ["single_agent_runtime", "tool_runtime_governance"]
                + (["multi_agent", "permission_recovery"] if collaborative else [])
            ),
        }
    elif layer == CapabilityLayer.APP:
        tree = _infer_app_tree(name, text, merged_metadata)
    else:
        tree = {
            "tier": "trunk",
            "slot": "tool_runtime_governance",
            "top_level": "tool_runtime_governance",
            "role": "runtime_capability",
            "depends_on_slots": [],
        }

    return _finalize_tree(tree)


def build_capability_tree_projection(capabilities: list[Capability]) -> dict[str, Any]:
    """Build a grouped capability tree from registered capabilities."""

    projection = deepcopy(_TREE_TEMPLATE)
    projection["unclassified"] = {"id": "unclassified", "label": "Unclassified", "capabilities": [], "count": 0}
    node_lookup: dict[str, dict[str, Any]] = {}

    for section_name in ("trunk", "execution_surfaces", "primary_branches", "secondary_branches"):
        for node in projection[section_name]:
            _init_projection_node(node)
            node_lookup[node["id"]] = node
            for child in node.get("children", []):
                _init_projection_node(child)
                node_lookup[child["id"]] = child

    capability_details: list[dict[str, Any]] = []
    for capability in sorted(capabilities, key=lambda item: (item.layer.value, item.name)):
        metadata = annotate_capability_tree_metadata(
            name=capability.name,
            layer=capability.layer,
            description=capability.description,
            tags=capability.tags,
            dependencies=capability.dependencies,
            provides=capability.provides,
            metadata=capability.metadata,
        )
        tree = dict(metadata.get("tree") or {})
        slot = str(tree.get("slot", "")).strip()
        detail = {
            "name": capability.name,
            "layer": capability.layer.value,
            "slot": slot,
            "top_level": tree.get("top_level", slot),
            "tier": tree.get("tier", "unclassified"),
            "role": tree.get("role", ""),
            "depends_on_slots": list(tree.get("depends_on_slots", [])),
            "dependencies": list(capability.dependencies),
            "provides": list(capability.provides),
            "tags": list(capability.tags),
        }
        capability_details.append(detail)

        node = node_lookup.get(slot)
        if node is None:
            projection["unclassified"]["capabilities"].append(capability.name)
            projection["unclassified"]["count"] += 1
            continue

        _attach_capability(node, capability.name)
        top_level = str(tree.get("top_level", slot)).strip()
        if top_level and top_level != slot and top_level in node_lookup:
            _attach_capability(node_lookup[top_level], capability.name)

    projection["capability_details"] = capability_details
    projection["relationships"] = _build_relationships(projection)
    return projection


def build_capability_tree_resume_projection(
    tree_projection: dict[str, Any] | None,
    *,
    max_capabilities_per_node: int = 3,
) -> dict[str, Any]:
    """Compress the full tree into a prompt-friendly resume projection."""

    if not isinstance(tree_projection, dict):
        return {}

    trunk_nodes = list(tree_projection.get("trunk", []))
    execution_nodes = list(tree_projection.get("execution_surfaces", []))
    primary_nodes = list(tree_projection.get("primary_branches", []))
    secondary_nodes = list(tree_projection.get("secondary_branches", []))

    trunk_chain = [str(node.get("label", "")).strip() for node in trunk_nodes if str(node.get("label", "")).strip()]
    execution_labels = [
        str(node.get("label", "")).strip() for node in execution_nodes if str(node.get("label", "")).strip()
    ]
    primary_summary = [
        _project_node_summary(node, max_capabilities=max_capabilities_per_node)
        for node in primary_nodes
        if isinstance(node, dict)
    ]
    secondary_labels = [
        str(node.get("label", "")).strip() for node in secondary_nodes if str(node.get("label", "")).strip()
    ]
    branch_labels = [item["label"] for item in primary_summary if item.get("label")]
    trunk_summary = " -> ".join(trunk_chain)
    execution_summary = (
        "Single-Agent Runtime builds on the trunk; Skills stay as a strategy overlay."
        if execution_labels
        else ""
    )

    return {
        "trunk_chain": trunk_chain,
        "trunk_summary": trunk_summary,
        "execution_surfaces": execution_labels,
        "execution_summary": execution_summary,
        "primary_branches": primary_summary,
        "secondary_branches": secondary_labels,
        "principles": [
            "Single-Agent Runtime is the common execution surface above the trunk.",
            "Skills extend strategy and prompting; they should not replace platform-runtime tools.",
            "Multi-Agent builds on Single-Agent plus permission/recovery.",
            "Workflow collaboration nodes depend on both workflow runtime and delegated agents.",
            "Apps split into app runtime, app modes, and app orchestration.",
        ],
        "route_hints": [
            {
                "topic": "start_here",
                "hint": (
                    "Start with trunk capabilities and the Single-Agent Runtime. Escalate to higher branches only "
                    "when the task needs durable structure, branching, or delegation."
                ),
            },
            {
                "topic": "create_app",
                "hint": (
                    "Prefer build_app_iteratively first, then create_app -> update_app_file -> "
                    "verify_app -> test_app_api. Use raw file tools only as a fallback."
                ),
            },
            {
                "topic": "workflow",
                "hint": (
                    "Use workflow capabilities for repeatable multi-step execution, branching, approvals, or "
                    "scheduling. Use collaboration nodes only when delegated experts add value."
                ),
            },
            {
                "topic": "knowledge_rag",
                "hint": (
                    "Use the knowledge/RAG branch when grounded retrieval or semantic memory is needed instead of "
                    "stuffing long reference context directly into the prompt."
                ),
            },
            {
                "topic": "web",
                "hint": (
                    "Use the web branch only when freshness, external URLs, or live search matter; otherwise stay "
                    "on local trunk capabilities."
                ),
            },
            {
                "topic": "skill_vs_platform",
                "hint": (
                    "Let skills guide strategy, but keep execution in platform tools when the platform "
                    "owns validation, metadata, or repair loops."
                ),
            },
            {
                "topic": "multi_agent",
                "hint": (
                    "Delegate only when a task is separable and Single-Agent execution is no longer the best path."
                ),
            },
        ],
        "branch_count": len(branch_labels),
        "secondary_branch_count": len(secondary_labels),
    }


def build_capability_route_projection(
    capabilities: list[Capability],
    *,
    query: str = "",
    provides: str = "",
    tree_projection: dict[str, Any] | None = None,
    projected_runtime_view: dict[str, Any] | None = None,
    max_matches: int = 5,
    max_route_hints: int = 4,
) -> dict[str, Any]:
    """Build a tree-aware routing recommendation for a task query."""

    effective_tree = (
        tree_projection if isinstance(tree_projection, dict) else build_capability_tree_projection(capabilities)
    )
    resume_projection = build_capability_tree_resume_projection(effective_tree)
    runtime_context = _coerce_route_runtime_context(projected_runtime_view)
    ranked = sorted(
        capabilities,
        key=lambda item: selection_metadata_for_capability(
            item,
            query=query,
            provides=provides,
            projected_runtime_view=projected_runtime_view,
        )["selection_sort_key"],
    )
    top_matches: list[dict[str, Any]] = []
    for capability in ranked[: max(1, int(max_matches))]:
        selection = selection_metadata_for_capability(
            capability,
            query=query,
            provides=provides,
            projected_runtime_view=projected_runtime_view,
        )
        top_matches.append(
            {
                "name": capability.name,
                "layer": capability.layer.value,
                "tree": dict(selection.get("tree", {})),
                "selection_score": int(selection.get("selection_score", 0) or 0),
                "selection_reason": str(selection.get("selection_reason", "")).strip(),
            }
        )

    recommended_tree = dict(top_matches[0].get("tree", {})) if top_matches else {}
    slot = str(recommended_tree.get("slot", "tool_runtime_governance")).strip() or "tool_runtime_governance"
    top_level = str(recommended_tree.get("top_level", slot)).strip() or slot
    tier = str(recommended_tree.get("tier", "trunk")).strip() or "trunk"
    slot_label = _NODE_INDEX.get(slot, {}).get("label", slot)
    top_level_label = _NODE_INDEX.get(top_level, {}).get("label", top_level)
    mode = _route_mode_for_slot(tier=tier, slot=slot)
    route_hints = _select_relevant_route_hints(
        list(resume_projection.get("route_hints", [])),
        query=query,
        top_level=top_level,
        slot=slot,
        top_matches=top_matches,
        max_hints=max_route_hints,
    )

    return {
        "query": query,
        "provides": provides,
        "candidate_count": len(ranked),
        "recommended": {
            "mode": mode,
            "tier": tier,
            "slot": slot,
            "slot_label": slot_label,
            "top_level": top_level,
            "top_level_label": top_level_label,
            "execution_surface": "single_agent_runtime",
            "summary": _build_route_summary(
                mode=mode,
                slot=slot,
                slot_label=slot_label,
                top_level=top_level,
                top_level_label=top_level_label,
                has_matches=bool(top_matches),
                runtime_context=runtime_context,
            ),
        },
        "top_matches": top_matches,
        "route_hints": route_hints,
        "runtime_constraints": runtime_context,
    }


def selection_metadata_for_capability(
    capability: Capability,
    *,
    query: str = "",
    provides: str = "",
    projected_runtime_view: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Return tree-aware selection metadata for one capability."""

    metadata = annotate_capability_tree_metadata(
        name=capability.name,
        layer=capability.layer,
        description=capability.description,
        tags=capability.tags,
        dependencies=capability.dependencies,
        provides=capability.provides,
        metadata=capability.metadata,
    )
    tree = dict(metadata.get("tree", {}))
    slot = str(tree.get("slot", "")).strip()
    tier = str(tree.get("tier", "secondary_branch")).strip() or "secondary_branch"
    top_level = str(tree.get("top_level", slot)).strip() or slot
    query_lower = str(query).strip().lower()
    provides_lower = str(provides).strip().lower()
    name_lower = capability.name.lower()
    provides_values = [str(item).strip().lower() for item in capability.provides if str(item).strip()]
    runtime_context = _coerce_route_runtime_context(projected_runtime_view)

    reasons: list[str] = []
    score = 0

    if provides_lower and provides_lower in provides_values:
        score += 100
        reasons.append("exact provider match")
    if query_lower:
        if query_lower == name_lower:
            score += 30
            reasons.append("exact capability match")
        elif query_lower in provides_values:
            score += 20
            reasons.append("capability provider match")

    if slot in set(runtime_context.get("prefer_slots", [])):
        score += 18
        reasons.append("runtime preference")
    if slot in set(runtime_context.get("avoid_slots", [])) or top_level in set(runtime_context.get("avoid_top_levels", [])):
        score -= 55
        reasons.append("runtime avoidance")
    branch_readiness = runtime_context.get("branch_readiness", {})
    branch_state = branch_readiness.get(top_level, {}) if isinstance(branch_readiness, dict) else {}
    if top_level and isinstance(branch_state, dict) and branch_state.get("ready") is False:
        score -= 1000
        reasons.append("branch not ready")
    if bool(runtime_context.get("force_trunk_first")) and tier != "trunk" and not query_lower and not provides_lower:
        score -= 30
        reasons.append("force trunk first")
    if runtime_context.get("permission_mode") == "plan" and slot in {
        "app_orchestration",
        "workflow_collaboration",
        "subagent_runtime",
    }:
        score -= 40
        reasons.append("plan mode guard")
    if bool(runtime_context.get("summary_active")) and slot in {"session_continuity", "context_hygiene"}:
        score += 10
        reasons.append("resume-first bias")
    if bool(runtime_context.get("summary_active")) and not bool(runtime_context.get("has_session_notebook")) and slot == "session_continuity":
        score += 14
        reasons.append("resume kernel missing")
    if bool(runtime_context.get("has_workspace_views")) and slot == "workspace_view":
        score += 6
        reasons.append("workspace continuity")
    if runtime_context.get("current_slot") == slot:
        score += 12
        reasons.append("current slot continuity")
    if runtime_context.get("active_task_count", 0) > 0 and runtime_context.get("current_top_level") == top_level:
        score += 14
        reasons.append("active task continuity")
    if top_level == "workflow_apps" and runtime_context.get("workflow_continuity"):
        score += 12
        reasons.append("workflow continuity")
    if slot.startswith("app_") and runtime_context.get("app_continuity"):
        score += 12
        reasons.append("app continuity")
    if top_level == "multi_agent" and runtime_context.get("multi_agent_continuity"):
        score += 16
        reasons.append("team memory continuity")
    if not bool(runtime_context.get("multi_agent_ready", True)) and (
        top_level == "multi_agent" or slot in {"subagent_runtime", "workflow_collaboration"}
    ):
        score -= 70
        reasons.append("isolation not ready")
    if not bool(runtime_context.get("delegation_ready", True)) and (
        top_level == "multi_agent" or slot in {"subagent_runtime", "workflow_collaboration"}
    ):
        score -= 110
        reasons.append("delegation not ready")
    if not bool(runtime_context.get("team_memory_ready", True)) and (
        top_level == "multi_agent" or slot in {"subagent_runtime", "workflow_collaboration"}
    ):
        score -= 45
        reasons.append("team memory not ready")
    if not query_lower and runtime_context.get("current_top_level") == top_level:
        score += 8
        reasons.append("current branch continuity")

    tier_priority = _TIER_SELECTION_PRIORITY.get(tier, 99)
    slot_priority = _SLOT_SELECTION_PRIORITY.get(slot, 99)
    if not query_lower and not provides_lower:
        score += max(0, 40 - (tier_priority * 10))
        reasons.append("default trunk-first ordering")

    return {
        "tree": tree,
        "selection_score": score,
        "selection_reason": ", ".join(reasons) if reasons else "default ordering",
        "selection_sort_key": (-score, tier_priority, slot_priority, capability.layer.value, capability.name.lower()),
    }


def _infer_tool_tree(name: str, text: str) -> dict[str, Any]:
    lower_name = name.lower().strip()
    if lower_name in _APP_RUNTIME_TOOL_NAMES:
        return {
            "tier": "primary_branch",
            "slot": "app_runtime",
            "top_level": "workflow_apps",
            "role": "app_runtime_tool",
            "depends_on_slots": ["tool_runtime_governance", "permission_recovery"],
        }
    if lower_name in _WORKSPACE_VIEW_TOOL_NAMES:
        return {
            "tier": "trunk",
            "slot": "workspace_view",
            "top_level": "workspace_view",
            "role": "workspace_view_tool",
            "depends_on_slots": ["tool_runtime_governance"],
        }
    if lower_name in _TOOL_RUNTIME_TOOL_NAMES:
        return {
            "tier": "trunk",
            "slot": "tool_runtime_governance",
            "top_level": "tool_runtime_governance",
            "role": "runtime_tool",
            "depends_on_slots": [],
        }
    if _contains_any(text, _PERMISSION_TOOL_TOKENS):
        return {
            "tier": "trunk",
            "slot": "permission_recovery",
            "top_level": "permission_recovery",
            "role": "permission_tool",
            "depends_on_slots": ["tool_runtime_governance", "session_continuity"],
        }
    if _contains_any(text, _HOOK_TOKENS):
        return {
            "tier": "secondary_branch",
            "slot": "hooks_runtime",
            "top_level": "hooks_runtime",
            "role": "hook_tool",
            "depends_on_slots": ["tool_runtime_governance", "context_hygiene"],
        }
    if _contains_any(text, _PLUGIN_TOKENS):
        return {
            "tier": "secondary_branch",
            "slot": "plugins_marketplace",
            "top_level": "plugins_marketplace",
            "role": "plugin_tool",
            "depends_on_slots": ["skill_strategy", "workflow_apps"],
        }
    if _contains_any(text, _WEB_TOKENS):
        return {
            "tier": "primary_branch",
            "slot": "web",
            "top_level": "web",
            "role": "web_tool",
            "depends_on_slots": ["tool_runtime_governance", "context_hygiene", "permission_recovery"],
        }
    if _contains_any(text, _KNOWLEDGE_TOKENS):
        return {
            "tier": "primary_branch",
            "slot": "knowledge_rag",
            "top_level": "knowledge_rag",
            "role": "knowledge_tool",
            "depends_on_slots": ["workspace_view", "session_continuity", "single_agent_runtime"],
        }
    if _contains_any(text, _MULTI_AGENT_TOKENS):
        return {
            "tier": "primary_branch",
            "slot": "subagent_runtime",
            "top_level": "multi_agent",
            "role": "delegation_tool",
            "depends_on_slots": ["single_agent_runtime", "session_continuity", "permission_recovery", "isolation_model"],
        }
    if _contains_any(text, _ISOLATION_TOKENS):
        return {
            "tier": "primary_branch",
            "slot": "isolation_model",
            "top_level": "multi_agent",
            "role": "isolation_tool",
            "depends_on_slots": ["tool_runtime_governance", "workspace_view", "permission_recovery"],
        }
    if _contains_any(text, _WORKFLOW_TOKENS):
        return {
            "tier": "primary_branch",
            "slot": "workflow_runtime",
            "top_level": "workflow_apps",
            "role": "workflow_tool",
            "depends_on_slots": ["single_agent_runtime", "tool_runtime_governance"],
        }
    if _contains_any(text, _APP_TOKENS):
        return {
            "tier": "primary_branch",
            "slot": "app_runtime",
            "top_level": "workflow_apps",
            "role": "app_tool",
            "depends_on_slots": ["tool_runtime_governance", "permission_recovery"],
        }
    if _contains_any(text, _UI_TOKENS):
        return {
            "tier": "secondary_branch",
            "slot": "ui_overlay",
            "top_level": "ui_overlay",
            "role": "ui_tool",
            "depends_on_slots": ["workflow_apps", "web"],
        }
    if _contains_any(text, _CONTEXT_TOOL_TOKENS):
        return {
            "tier": "trunk",
            "slot": "context_hygiene",
            "top_level": "context_hygiene",
            "role": "context_tool",
            "depends_on_slots": ["tool_runtime_governance", "workspace_view"],
        }
    if _contains_any(text, _SESSION_TOOL_TOKENS):
        return {
            "tier": "trunk",
            "slot": "session_continuity",
            "top_level": "session_continuity",
            "role": "session_tool",
            "depends_on_slots": ["workspace_view", "context_hygiene"],
        }
    return {
        "tier": "trunk",
        "slot": "tool_runtime_governance",
        "top_level": "tool_runtime_governance",
        "role": "runtime_tool",
        "depends_on_slots": [],
    }


def _project_node_summary(node: dict[str, Any], *, max_capabilities: int) -> dict[str, Any]:
    dependencies = [
        _NODE_INDEX.get(dep, {}).get("label", dep) for dep in list(node.get("depends_on", [])) if str(dep).strip()
    ]
    children = [
        str(child.get("label", "")).strip() for child in node.get("children", []) if str(child.get("label", "")).strip()
    ]
    return {
        "id": str(node.get("id", "")).strip(),
        "label": str(node.get("label", "")).strip(),
        "depends_on": dependencies,
        "children": children,
        "capabilities": list(node.get("capabilities", []))[: max(1, int(max_capabilities))],
        "capability_count": int(node.get("capability_count", 0) or 0),
    }


def _coerce_route_runtime_context(projected_runtime_view: dict[str, Any] | None) -> dict[str, Any]:
    view = coerce_projected_runtime_view(projected_runtime_view)
    if view is None:
        permission: dict[str, Any] = {}
        settings: dict[str, Any] = {}
        session: dict[str, Any] = {}
        workspace: dict[str, Any] = {}
        tasks: dict[str, Any] = {}
        context_hygiene: dict[str, Any] = {}
        route: dict[str, Any] = {}
        isolation: dict[str, Any] = {}
        team_memory: dict[str, Any] = {}
    else:
        permission = dict(view.permission)
        settings = dict(view.settings)
        session = dict(view.session)
        workspace = dict(view.workspace)
        tasks = dict(view.tasks)
        context_hygiene = dict(view.context_hygiene)
        route = dict(view.route)
        isolation = dict(view.isolation)
        team_memory = dict(view.team_memory)
    recommended = route.get("recommended", {}) if isinstance(route.get("recommended"), dict) else {}
    status_counts = dict(tasks.get("status_counts", {})) if isinstance(tasks.get("status_counts"), dict) else {}
    active_task_count = int(status_counts.get("pending", 0) or 0) + int(status_counts.get("in_progress", 0) or 0)
    recent_activity_kinds = [
        str(item.get("kind", "")).strip()
        for item in tasks.get("activities", [])
        if isinstance(item, dict) and str(item.get("kind", "")).strip()
    ]
    permission_mode = str(permission.get("mode") or settings.get("permission_mode") or "default").strip().lower()
    permission_mode = permission_mode or "default"
    multi_agent_ready = bool(isolation.get("multi_agent_ready", True))
    delegation_ready = bool(isolation.get("delegation_ready", multi_agent_ready))
    isolation_ready = bool(isolation.get("isolation_ready", multi_agent_ready))
    permission_ready = bool(isolation.get("permission_ready", True))
    workspace_ready = bool(isolation.get("workspace_ready", True))
    artifact_ownership_ready = bool(isolation.get("artifact_ownership_ready", True))
    recovery_ready = bool(isolation.get("recovery_ready", True))
    workflow_continuity = bool(
        str(recommended.get("top_level", "")).strip() == "workflow_apps"
        or any(kind in {"workflow_run", "durable_task"} for kind in recent_activity_kinds)
    )
    app_continuity = bool(
        str(recommended.get("slot", "")).strip().startswith("app_")
        or any(kind in {"file_view", "tool_run"} for kind in recent_activity_kinds)
    )
    team_memory_ready = not bool(team_memory) or bool(team_memory.get("shared_memory_ready"))
    team_active_run_count = int(team_memory.get("active_run_count", 0) or 0)
    team_note_count = int(team_memory.get("note_count", 0) or 0)
    multi_agent_continuity = bool(team_active_run_count or team_note_count)
    branch_readiness = {
        "web": {"ready": True, "reasons": []},
        "knowledge_rag": {"ready": True, "reasons": []},
        "workflow_apps": {
            "ready": permission_mode != "plan",
            "reasons": [] if permission_mode != "plan" else ["plan mode keeps workflow/app execution in analysis-first state"],
        },
        "multi_agent": {
            "ready": (
                multi_agent_ready
                and delegation_ready
                and isolation_ready
                and permission_ready
                and workspace_ready
                and artifact_ownership_ready
                and recovery_ready
                and team_memory_ready
            ),
            "reasons": [
                reason
                for ready, reason in (
                    (multi_agent_ready, "multi-agent substrate is not enabled"),
                    (delegation_ready, "delegation contract is not ready"),
                    (isolation_ready, "isolation contract is incomplete"),
                    (permission_ready, "permission scope is not ready"),
                    (workspace_ready, "workspace execution target is not ready"),
                    (artifact_ownership_ready, "artifact ownership is not resolved"),
                    (recovery_ready, "recovery/audit state is not ready"),
                    (team_memory_ready, "team memory continuity is not ready"),
                )
                if not ready
            ],
        },
    }
    return {
        "permission_mode": permission_mode,
        "has_session_notebook": bool(str(session.get("session_notebook_summary", "")).strip()),
        "has_workspace_views": bool(workspace.get("recent_views")),
        "active_task_count": active_task_count,
        "recent_activity_kinds": list(dict.fromkeys(recent_activity_kinds))[:8],
        "prefer_slots": [str(item).strip() for item in route.get("prefer_slots", []) if str(item).strip()],
        "avoid_slots": [str(item).strip() for item in route.get("avoid_slots", []) if str(item).strip()],
        "avoid_top_levels": [str(item).strip() for item in route.get("avoid_top_levels", []) if str(item).strip()],
        "force_trunk_first": bool(route.get("force_trunk_first")),
        "current_slot": str(recommended.get("slot", "")).strip(),
        "current_top_level": str(recommended.get("top_level", "")).strip(),
        "summary_active": bool(context_hygiene.get("summary_active")),
        "multi_agent_ready": multi_agent_ready,
        "delegation_ready": delegation_ready,
        "isolation_ready": isolation_ready,
        "permission_ready": permission_ready,
        "workspace_ready": workspace_ready,
        "artifact_ownership_ready": artifact_ownership_ready,
        "recovery_ready": recovery_ready,
        "workflow_continuity": workflow_continuity,
        "app_continuity": app_continuity,
        "multi_agent_continuity": multi_agent_continuity,
        "team_memory_ready": team_memory_ready,
        "team_active_run_count": team_active_run_count,
        "team_note_count": team_note_count,
        "branch_readiness": branch_readiness,
    }


def _route_mode_for_slot(*, tier: str, slot: str) -> str:
    if tier == "trunk":
        return "trunk_first"
    if slot == "skill_strategy" or tier == "execution_surface":
        return "execution_surface"
    return "branch_on_demand"


def _build_route_summary(
    *,
    mode: str,
    slot: str,
    slot_label: str,
    top_level: str,
    top_level_label: str,
    has_matches: bool,
    runtime_context: dict[str, Any] | None = None,
) -> str:
    route_context = dict(runtime_context or {})
    if not has_matches:
        return "Start with trunk capabilities and the Single-Agent Runtime, then branch only if the task clearly needs it."
    if mode == "trunk_first":
        return f"Stay on the trunk through {slot_label} first, and escalate to a branch only if the task grows beyond the local execution spine."
    if mode == "execution_surface":
        return "Use the Single-Agent Runtime as the execution surface; let skills guide strategy, but keep execution in platform-managed tools."
    if route_context.get("permission_mode") == "plan" and top_level in {"workflow_apps", "multi_agent"}:
        return (
            f"Plan mode is active, so treat {top_level_label} as a later branch. "
            "Prefer trunk analysis and session continuity first."
        )
    branch_readiness = route_context.get("branch_readiness", {})
    branch_state = branch_readiness.get(top_level, {}) if isinstance(branch_readiness, dict) else {}
    if top_level and isinstance(branch_state, dict) and branch_state.get("ready") is False:
        reasons = ", ".join(str(item).strip() for item in branch_state.get("reasons", []) if str(item).strip())
        if reasons:
            return f"{top_level_label} is gated by runtime readiness: {reasons}. Stay on the trunk until that branch becomes executable."
        return f"{top_level_label} is not runtime-ready yet, so stay on the trunk until its dependencies are satisfied."
    if bool(route_context.get("summary_active")) and not bool(route_context.get("has_session_notebook")):
        return "Context is compacted and the resume kernel is thin; rebuild from session continuity before opening a higher branch."
    if not bool(route_context.get("multi_agent_ready", True)) and top_level == "multi_agent":
        return "Multi-Agent routing is gated by the isolation model; keep work on the trunk until sandboxed delegation is ready."
    if not bool(route_context.get("delegation_ready", True)) and top_level == "multi_agent":
        return "Isolation requirements are stricter than the current runtime contract, so keep work on the trunk until delegation becomes contract-ready."
    if top_level == "workflow_apps":
        if slot.startswith("app_"):
            return "Enter the Workflow / Apps / Automation branch through app runtime first; keep app creation and repair in the managed app toolchain."
        if slot.startswith("workflow_"):
            return "Enter the Workflow / Apps / Automation branch through workflow runtime; use collaboration nodes only when delegated agents add clear value."
    if top_level == "multi_agent":
        return "Escalate into Multi-Agent only because the task is separable; Single-Agent remains the default execution surface underneath."
    if top_level == "knowledge_rag":
        return "Take the Knowledge / RAG branch for grounded retrieval instead of pushing long reference context directly into the prompt."
    if top_level == "web":
        return "Take the Web / External Info branch only because freshness or external content matters for this task."
    return f"Enter the {top_level_label} branch from the Single-Agent Runtime and keep the rest of the tree as support layers."


def _select_relevant_route_hints(
    route_hints: list[dict[str, Any]],
    *,
    query: str,
    top_level: str,
    slot: str,
    top_matches: list[dict[str, Any]],
    max_hints: int,
) -> list[dict[str, Any]]:
    if not route_hints:
        return []

    query_lower = str(query).strip().lower()
    topics = {"start_here"}
    if top_level == "workflow_apps":
        topics.add("workflow")
        topics.add("skill_vs_platform")
        if slot.startswith("app_") or "app" in query_lower:
            topics.add("create_app")
    if top_level == "knowledge_rag" or any(token in query_lower for token in ("rag", "retriev", "semantic", "knowledge")):
        topics.add("knowledge_rag")
    if top_level == "web" or any(token in query_lower for token in ("web", "url", "fetch", "search", "latest")):
        topics.add("web")
    if top_level == "multi_agent" or any(
        token in query_lower for token in ("delegate", "subagent", "multi-agent", "swarm", "coordinator")
    ):
        topics.add("multi_agent")
    if any(item.get("layer") == "skill" or item.get("tree", {}).get("slot") == "skill_strategy" for item in top_matches):
        topics.add("skill_vs_platform")

    filtered = [item for item in route_hints if str(item.get("topic", "")).strip() in topics]
    if not filtered:
        filtered = route_hints[:1]
    return filtered[: max(1, int(max_hints))]


def _infer_app_tree(name: str, text: str, metadata: dict[str, Any]) -> dict[str, Any]:
    mode = str(metadata.get("mode", "static")).strip().lower() or "static"
    has_orchestration_signal = _contains_any(
        text,
        ("matrix", "topology", "orchestration", "router", "mesh"),
    )
    if has_orchestration_signal:
        slot = "app_orchestration"
        depends_on = ["app_runtime", "workflow_runtime", "multi_agent"]
        role = "orchestrated_app"
    elif mode in {"assistant", "chat", "rag", "workflow"} or metadata.get("agent_binding") or metadata.get("workflow_binding"):
        slot = "app_modes"
        depends_on = ["app_runtime", "single_agent_runtime"]
        role = f"{mode}_app"
        if mode == "rag" or metadata.get("knowledge_collections"):
            depends_on.append("knowledge_rag")
        if mode == "workflow" or metadata.get("workflow_binding"):
            depends_on.append("workflow_runtime")
    else:
        slot = "app_runtime"
        depends_on = ["tool_runtime_governance", "permission_recovery"]
        role = "static_app"
    return {
        "tier": "primary_branch",
        "slot": slot,
        "top_level": "workflow_apps",
        "role": role,
        "depends_on_slots": _unique_strings(depends_on),
    }


def _infer_supported_branches(text: str) -> list[str]:
    supports: list[str] = []
    if _contains_any(text, _WEB_TOKENS):
        supports.append("web")
    if _contains_any(text, _KNOWLEDGE_TOKENS):
        supports.append("knowledge_rag")
    if _contains_any(text, _WORKFLOW_TOKENS + _APP_TOKENS):
        supports.append("workflow_apps")
    if _contains_any(text, _MULTI_AGENT_TOKENS + ("agent",)):
        supports.append("multi_agent")
    return supports or ["web", "knowledge_rag", "workflow_apps", "multi_agent"]


def _finalize_tree(tree: dict[str, Any]) -> dict[str, Any]:
    slot = str(tree.get("slot", "")).strip()
    top_level = str(tree.get("top_level", slot)).strip() or slot
    finalized = dict(tree)
    finalized["slot"] = slot
    finalized["top_level"] = top_level
    finalized["depends_on_slots"] = _unique_strings(finalized.get("depends_on_slots", []))
    if "supports" in finalized:
        finalized["supports"] = _unique_strings(finalized.get("supports", []))
    node = _NODE_INDEX.get(slot)
    if node:
        finalized.setdefault("label", node.get("label", slot))
        finalized.setdefault("node_description", node.get("description", ""))
    return finalized


def _build_relationships(projection: dict[str, Any]) -> list[dict[str, str]]:
    relationships: list[dict[str, str]] = []
    for section_name in ("trunk", "execution_surfaces", "primary_branches", "secondary_branches"):
        for node in projection[section_name]:
            relationships.extend(_node_relationships(node))
    return relationships


def _node_relationships(node: dict[str, Any]) -> list[dict[str, str]]:
    edges = [
        {"from": node["id"], "to": dependency, "type": "depends_on"}
        for dependency in node.get("depends_on", [])
    ]
    for child in node.get("children", []):
        edges.append({"from": child["id"], "to": node["id"], "type": "belongs_to"})
        edges.extend(_node_relationships(child))
    return edges


def _init_projection_node(node: dict[str, Any]) -> None:
    node["capabilities"] = []
    node["capability_count"] = 0


def _attach_capability(node: dict[str, Any], capability_name: str) -> None:
    if capability_name in node["capabilities"]:
        return
    node["capabilities"].append(capability_name)
    node["capability_count"] += 1


def _unique_strings(values: list[Any]) -> list[str]:
    seen: set[str] = set()
    results: list[str] = []
    for value in values:
        item = str(value).strip()
        if not item or item in seen:
            continue
        seen.add(item)
        results.append(item)
    return results


def _contains_any(text: str, tokens: tuple[str, ...] | list[str]) -> bool:
    return any(token in text for token in tokens)


def _text_blob(*parts: Any) -> str:
    flattened: list[str] = []
    for part in parts:
        if isinstance(part, (list, tuple, set)):
            flattened.extend(_text_blob(item) for item in part)
        elif isinstance(part, dict):
            flattened.append(_text_blob(part.keys()))
            flattened.append(_text_blob(part.values()))
        elif part is None:
            continue
        else:
            flattened.append(str(part).lower())
    return " ".join(item for item in flattened if item).lower()
