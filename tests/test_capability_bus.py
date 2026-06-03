from __future__ import annotations

import json
from types import SimpleNamespace

from core.systems.bus.capability_bus import CapabilityBus, CapabilityLayer, get_capability_bus_tools
from core.systems.bus.capability_bus_models import EventType


def _tree_nodes(projection):
    nodes = {}
    for section in ("trunk", "execution_surfaces", "primary_branches", "secondary_branches"):
        for node in projection[section]:
            nodes[node["id"]] = node
            for child in node.get("children", []):
                nodes[child["id"]] = child
    return nodes


def test_capability_bus_registers_persists_and_tracks_context(temp_paths):
    bus = CapabilityBus(str(temp_paths.workspace_dir))
    seen: list[str] = []
    bus.on(EventType.CAPABILITY_REGISTERED, lambda event: seen.append(event.source))

    bus.register("calc", CapabilityLayer.TOOL, description="math helper")
    bus.register(
        "planner",
        CapabilityLayer.AGENT,
        description="planning agent",
        dependencies=["calc"],
        tags=["sub-agent"],
    )
    bus.record_invocation("planner", success=True, duration_ms=25)
    bus.share_context("phase", "draft", source="planner")
    bus.share_data("artifact", {"ok": True}, source="planner")
    bus.save_registry()

    registry_path = temp_paths.workspace_dir / "data" / "capability_registry.json"
    payload = json.loads(registry_path.read_text(encoding="utf-8"))
    last_execution = bus.get_context("last_capability_execution")

    assert seen == ["calc", "planner"]
    assert bus.get_context("phase") == "draft"
    assert bus.get_data("artifact") == {"ok": True}
    assert last_execution["name"] == "planner"
    assert last_execution["layer"] == "agent"
    assert last_execution["status"] == "completed"
    assert payload["capabilities"]["planner"]["success_rate"] == "100%"
    assert bus.resolve_dependencies("planner")["resolved"] is True


def test_capability_bus_load_persisted_snapshot_on_startup(temp_paths):
    """Registry is restored from disk on init; stats and provenance survive restart."""
    bus1 = CapabilityBus(str(temp_paths.workspace_dir))
    bus1.register("my_tool", CapabilityLayer.TOOL, description="a tool", registered_by="auto_register_tools")
    bus1.record_invocation("my_tool", success=True, duration_ms=42)
    bus1.record_invocation("my_tool", success=False, duration_ms=10)
    bus1.save_registry()

    bus2 = CapabilityBus(str(temp_paths.workspace_dir))
    restored = bus2.get("my_tool")
    assert restored is not None
    assert restored.invoke_count == 2
    assert restored.success_count == 1
    assert restored.total_duration_ms == 52.0
    assert restored.registered_by == "auto_register_tools"


def test_capability_bus_auto_register_preserves_loaded_stats(temp_paths):
    """When auto_register re-registers a capability, prior stats from snapshot are preserved."""
    bus1 = CapabilityBus(str(temp_paths.workspace_dir))
    bus1.register("exec_code", CapabilityLayer.TOOL, description="execute code")
    bus1.record_invocation("exec_code", success=True, duration_ms=100)
    bus1.save_registry()

    bus2 = CapabilityBus(str(temp_paths.workspace_dir))
    bus2.register("exec_code", CapabilityLayer.TOOL, description="execute code (v2)")
    updated = bus2.get("exec_code")
    assert updated is not None
    assert updated.description == "execute code (v2)"
    assert updated.invoke_count == 1
    assert updated.total_duration_ms == 100.0


def test_capability_from_dict_roundtrip():
    from core.systems.bus.capability_bus_models import Capability

    cap = Capability(
        name="test",
        layer=CapabilityLayer.SKILL,
        description="d",
        tags=["a"],
        invoke_count=5,
        success_count=3,
        total_duration_ms=250.0,
        registered_by="test_source",
        origin_path="/skills/test",
    )
    d = cap.to_dict()
    restored = Capability.from_dict(d)
    assert restored.name == "test"
    assert restored.layer == CapabilityLayer.SKILL
    assert restored.invoke_count == 5
    assert restored.total_duration_ms == 250.0
    assert restored.registered_by == "test_source"
    assert restored.origin_path == "/skills/test"


def test_capability_bus_tool_interface_queries_registered_skills(temp_paths):
    bus = CapabilityBus(str(temp_paths.workspace_dir))
    skill_registry = SimpleNamespace(
        skills={
            "qa_skill": SimpleNamespace(
                description="Question answering",
                tags=["analysis"],
                tools=[SimpleNamespace(name="lookup")],
            )
        }
    )
    bus.auto_register_skills(skill_registry)
    tool = get_capability_bus_tools(bus)[0]

    listed = json.loads(tool._run(action="find", layer="skill"))
    share_result = json.loads(tool._run(action="share", key="memo", value='{"ready": true}'))
    fetched = json.loads(tool._run(action="get", key="memo"))

    assert listed[0]["name"] == "qa_skill"
    assert listed[0]["provides"] == ["lookup"]
    assert share_result["success"] is True
    assert fetched["data"] == {"ready": True}


def test_capability_bus_tree_projection_groups_trunk_and_branch_capabilities(temp_paths):
    bus = CapabilityBus(str(temp_paths.workspace_dir))
    bus.auto_register_tools(
        [
            SimpleNamespace(name="read_file", description="Read workspace files"),
            SimpleNamespace(name="compact_conversation", description="Compact context and history"),
            SimpleNamespace(name="web_fetch", description="Fetch URL content from the web"),
        ]
    )

    projection = bus.get_tree_projection()
    nodes = _tree_nodes(projection)

    assert "read_file" in nodes["workspace_view"]["capabilities"]
    assert "compact_conversation" in nodes["context_hygiene"]["capabilities"]
    assert "web_fetch" in nodes["web"]["capabilities"]
    assert any(item["name"] == "web_fetch" and item["slot"] == "web" for item in projection["capability_details"])


def test_capability_bus_auto_register_agents_and_apps_capture_tree_dependencies(temp_paths):
    from core.modes.apps.app_manager import AppManager

    bus = CapabilityBus(str(temp_paths.workspace_dir))
    bus.register("triage_flow", CapabilityLayer.WORKFLOW, description="workflow runtime")
    agent_storage = SimpleNamespace(
        agents={
            "reviewer": SimpleNamespace(
                role="Review code",
                team_role="reviewer",
                tools=["read_file", "grep_files"],
                workflows=["triage_flow"],
                capabilities=["analysis"],
                knowledge=SimpleNamespace(enabled=False),
            )
        }
    )
    bus.auto_register_agents(agent_storage)

    app_manager = AppManager(str(temp_paths.apps_dir), project_paths=temp_paths)
    app_manager.create_app(
        "review_hub",
        description="Assistant review app",
        mode="assistant",
        agent_binding="reviewer",
        workflow_binding="triage_flow",
    )
    bus.auto_register_apps(app_manager)

    reviewer = bus.get("reviewer")
    review_hub = bus.get("review_hub")

    assert reviewer is not None
    assert reviewer.dependencies == ["read_file", "grep_files", "triage_flow"]
    assert reviewer.metadata["tree"]["slot"] == "subagent_runtime"
    assert reviewer.metadata["tree"]["top_level"] == "multi_agent"

    assert review_hub is not None
    assert review_hub.dependencies == ["reviewer", "triage_flow"]
    assert review_hub.metadata["tree"]["slot"] == "app_modes"
    assert review_hub.metadata["tree"]["top_level"] == "workflow_apps"


def test_capability_bus_tool_interface_supports_tree_action(temp_paths):
    bus = CapabilityBus(str(temp_paths.workspace_dir))
    bus.auto_register_tools([SimpleNamespace(name="create_app", description="Create managed apps")])
    tool = get_capability_bus_tools(bus)[0]

    tree = json.loads(tool._run(action="tree"))
    nodes = _tree_nodes(tree)

    assert "create_app" in nodes["app_runtime"]["capabilities"]


def test_capability_bus_tool_interface_supports_route_action(temp_paths):
    bus = CapabilityBus(str(temp_paths.workspace_dir))
    bus.auto_register_tools(
        [
            SimpleNamespace(name="read_file", description="Read workspace files"),
            SimpleNamespace(name="build_app_iteratively", description="Build managed apps iteratively"),
            SimpleNamespace(name="delegate_to_agent", description="Delegate work to a subagent"),
        ]
    )
    tool = get_capability_bus_tools(bus)[0]

    route = json.loads(tool._run(action="route", query="build_app_iteratively", limit=3))

    assert route["recommended"]["mode"] == "branch_on_demand"
    assert route["recommended"]["top_level"] == "workflow_apps"
    assert route["top_matches"][0]["name"] == "build_app_iteratively"
    assert "create_app" in [item["topic"] for item in route["route_hints"]]


def test_capability_bus_route_uses_projected_runtime_view_constraints(temp_paths):
    bus = CapabilityBus(str(temp_paths.workspace_dir))
    bus.auto_register_tools(
        [
            SimpleNamespace(name="read_file", description="Read workspace files"),
            SimpleNamespace(name="delegate_to_agent", description="Delegate work to a subagent"),
            SimpleNamespace(name="build_app_iteratively", description="Build managed apps iteratively"),
        ]
    )
    bus.share_context(
        "projected_runtime_view",
        {
            "permission": {"mode": "plan"},
            "route": {
                "force_trunk_first": True,
                "prefer_slots": ["workspace_view"],
                "avoid_slots": ["subagent_runtime"],
            },
            "context_hygiene": {"summary_active": True},
            "isolation": {"multi_agent_ready": False},
        },
        source="test",
    )

    route = bus.get_route_projection(query="", provides="", max_matches=3)

    assert route["recommended"]["mode"] == "trunk_first"
    assert route["top_matches"][0]["name"] == "read_file"
    assert route["runtime_constraints"]["permission_mode"] == "plan"


def test_capability_bus_route_blocks_multi_agent_when_delegation_contract_is_not_ready(temp_paths):
    bus = CapabilityBus(str(temp_paths.workspace_dir))
    bus.auto_register_tools(
        [
            SimpleNamespace(name="read_file", description="Read workspace files"),
            SimpleNamespace(name="delegate_to_agent", description="Delegate work to a subagent"),
        ]
    )
    bus.share_context(
        "projected_runtime_view",
        {
            "permission": {"mode": "default"},
            "route": {"recommended": {"slot": "workspace_view", "top_level": "workspace_view"}},
            "isolation": {
                "multi_agent_ready": True,
                "delegation_ready": False,
                "requires_strict_isolation": True,
                "visibility": "project",
            },
        },
        source="test",
    )

    route = bus.get_route_projection(query="delegate this task", provides="", max_matches=2)

    assert route["runtime_constraints"]["delegation_ready"] is False
    assert route["top_matches"][0]["name"] == "read_file"
    assert route["recommended"]["top_level"] != "multi_agent"


def test_capability_bus_route_exposes_branch_readiness_for_workflow_apps(temp_paths):
    bus = CapabilityBus(str(temp_paths.workspace_dir))
    bus.auto_register_tools(
        [
            SimpleNamespace(name="read_file", description="Read workspace files"),
            SimpleNamespace(name="build_app_iteratively", description="Build managed apps iteratively"),
        ]
    )
    bus.share_context(
        "projected_runtime_view",
        {
            "permission": {"mode": "plan"},
            "route": {"recommended": {"slot": "workspace_view", "top_level": "workspace_view"}},
            "isolation": {"multi_agent_ready": True, "delegation_ready": True},
        },
        source="test",
    )

    route = bus.get_route_projection(query="build an app", provides="", max_matches=2)

    assert route["runtime_constraints"]["branch_readiness"]["workflow_apps"]["ready"] is False
    assert route["runtime_constraints"]["branch_readiness"]["workflow_apps"]["reasons"]


def test_capability_bus_route_uses_team_memory_continuity_for_multi_agent(temp_paths):
    bus = CapabilityBus(str(temp_paths.workspace_dir))
    bus.auto_register_tools(
        [
            SimpleNamespace(name="read_file", description="Read workspace files"),
            SimpleNamespace(name="delegate_to_agent", description="Delegate work to a subagent"),
        ]
    )
    bus.share_context(
        "projected_runtime_view",
        {
            "permission": {"mode": "default"},
            "route": {"recommended": {"slot": "subagent_runtime", "top_level": "multi_agent"}},
            "isolation": {
                "multi_agent_ready": True,
                "delegation_ready": True,
                "isolation_ready": True,
                "permission_ready": True,
                "workspace_ready": True,
                "artifact_ownership_ready": True,
                "recovery_ready": True,
            },
            "team_memory": {
                "shared_memory_ready": True,
                "active_run_count": 1,
                "note_count": 2,
            },
        },
        source="test",
    )

    route = bus.get_route_projection(query="delegate this task", provides="", max_matches=2)

    assert route["runtime_constraints"]["multi_agent_continuity"] is True
    assert route["top_matches"][0]["name"] == "delegate_to_agent"
    assert route["recommended"]["top_level"] == "multi_agent"
