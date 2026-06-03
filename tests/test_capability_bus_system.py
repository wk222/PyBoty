from __future__ import annotations

import json
import os
import tempfile
import time
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

try:
    from langchain_core.messages import ToolMessage
    from langgraph.types import Command
    _HAS_LC = True
except ImportError:
    _HAS_LC = False

from core.assets.agents.storage import AgentStorage
from core.assets.skills import SkillRegistry
from core.assets.skills.skill_marketplace import SkillMarketplace
from core.assets.skills.skill_registry import SkillRegistry as SkillRegistryClass
from core.systems.apps.app_manager import AppManager
from core.systems.capability.capability_bus import CapabilityBus, CapabilityLayer, get_capability_bus_tools
from core.systems.capability.capability_bus_models import EventType, Capability
from core.systems.capability.capability_registry import CapabilityRegistry, get_capability_registry_tools
from core.systems.capability.lc_bus_middleware import LCBusMiddleware
from core.systems.integration.mcp.mcp_hub import (
    MCPHub,
    MCPResourceDescriptor,
    MCPServerConfig,
    MCPServerConnection,
    MCPToolAdapter,
    MCPToolDescriptor,
)
from core.systems.runtime.runtime_capability_bundle import build_capability_runtime_bundle


# ---------------------------------------------------------------------------
# Setup & Helper Functions
# ---------------------------------------------------------------------------

def _tree_nodes(projection):
    nodes = {}
    for section in ("trunk", "execution_surfaces", "primary_branches", "secondary_branches"):
        for node in projection[section]:
            nodes[node["id"]] = node
            for child in node.get("children", []):
                nodes[child["id"]] = child
    return nodes


def _write_skill(root, name: str, description: str) -> None:
    skill_dir = root / name
    skill_dir.mkdir(parents=True, exist_ok=True)
    skill_dir.joinpath("SKILL.md").write_text(
        f"---\nname: {name}\ndescription: {description}\n---\n\nUse this skill.\n",
        encoding="utf-8",
    )


# ---------------------------------------------------------------------------
# 1. Capability Bus Tests (formerly test_capability_bus.py)
# ---------------------------------------------------------------------------

class TestCapabilityBusClass:
    def test_capability_bus_registers_persists_and_tracks_context(self, temp_paths):
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

    def test_capability_bus_load_persisted_snapshot_on_startup(self, temp_paths):
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

    def test_capability_bus_auto_register_preserves_loaded_stats(self, temp_paths):
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

    def test_capability_from_dict_roundtrip(self):
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

    def test_capability_bus_tool_interface_queries_registered_skills(self, temp_paths):
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

    def test_capability_bus_tree_projection_groups_trunk_and_branch_capabilities(self, temp_paths):
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

    def test_capability_bus_auto_register_agents_and_apps_capture_tree_dependencies(self, temp_paths):
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

    def test_capability_bus_tool_interface_supports_tree_action(self, temp_paths):
        bus = CapabilityBus(str(temp_paths.workspace_dir))
        bus.auto_register_tools([SimpleNamespace(name="create_app", description="Create managed apps")])
        tool = get_capability_bus_tools(bus)[0]

        tree = json.loads(tool._run(action="tree"))
        nodes = _tree_nodes(tree)

        assert "create_app" in nodes["app_runtime"]["capabilities"]

    def test_capability_bus_tool_interface_supports_route_action(self, temp_paths):
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

    def test_capability_bus_route_uses_projected_runtime_view_constraints(self, temp_paths):
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

    def test_capability_bus_route_blocks_multi_agent_when_delegation_contract_is_not_ready(self, temp_paths):
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

    def test_capability_bus_route_exposes_branch_readiness_for_workflow_apps(self, temp_paths):
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

    def test_capability_bus_route_uses_team_memory_continuity_for_multi_agent(self, temp_paths):
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


# ---------------------------------------------------------------------------
# 2. Capability Registry Tests (formerly test_capability_registry.py)
# ---------------------------------------------------------------------------

class TestCapabilityRegistryClass:
    def test_capability_registry_discovers_local_and_marketplace_entries(self, temp_paths):
        workspace_skills = temp_paths.workspace_dir / "skills"
        _write_skill(workspace_skills, "pdf_skill", "Extract PDF tables")

        skill_registry = SkillRegistry(str(workspace_skills))
        marketplace = SkillMarketplace(str(temp_paths.workspace_dir))
        packaged = marketplace.package_skill("pdf_skill")
        assert packaged["success"] is True

        app_manager = AppManager(str(temp_paths.apps_dir), project_paths=temp_paths)
        app_manager.create_app("billing", description="Billing app", tags=["finance"])

        bus = CapabilityBus(str(temp_paths.workspace_dir))
        registry = CapabilityRegistry(
            workspace_dir=temp_paths.workspace_dir,
            capability_bus=bus,
            skill_marketplace=marketplace,
            skill_registry=skill_registry,
            app_manager=app_manager,
        )
        snapshot = registry.refresh_local_index(save=True)
        discovery = registry.discover(query="pdf")

        assert snapshot["stats"]["total_capabilities"] >= 2
        assert any(item["name"] == "pdf_skill" for item in discovery["local"])
        assert any(item["name"] == "pdf_skill" for item in discovery["marketplace"])

    def test_capability_registry_contract_and_publish(self, temp_paths, monkeypatch):
        workspace_skills = temp_paths.workspace_dir / "skills"
        _write_skill(workspace_skills, "planner_skill", "Plan tasks")

        skill_registry = SkillRegistry(str(workspace_skills))
        marketplace = SkillMarketplace(str(temp_paths.workspace_dir))
        bus = CapabilityBus(str(temp_paths.workspace_dir))
        bus.register(
            "shared_planner",
            CapabilityLayer.AGENT,
            description="Shared planning agent",
            provides=["plan_tasks"],
            metadata={"schema": {"input": "goal", "output": "plan"}},
        )

        published_events: list[tuple[str, dict[str, object]]] = []

        def _emit(event):
            published_events.append((event.type.value, event.payload))

        monkeypatch.setattr("core.systems.capability.capability_registry.event_bus.emit", _emit)

        registry = CapabilityRegistry(
            workspace_dir=temp_paths.workspace_dir,
            capability_bus=bus,
            skill_marketplace=marketplace,
            skill_registry=skill_registry,
        )

        contract = registry.get_capability_contract("shared_planner")
        result = registry.publish_skill("planner_skill")

        assert contract is not None
        assert contract["interface"]["schema"]["output"] == "plan"
        assert contract["tree"]["slot"] == "subagent_runtime"
        assert result["success"] is True
        assert any(event_type == "capability_published" for event_type, _ in published_events)

    def test_capability_registry_persists_grants_across_restart(self, temp_paths):
        app_manager = AppManager(str(temp_paths.apps_dir), project_paths=temp_paths)
        app_manager.create_app("parser", description="Parser app", exports=["parse.doc"])
        app_manager.create_app("crm", description="Caller app")
        app_manager.reload_apps()

        marketplace = SkillMarketplace(str(temp_paths.workspace_dir))
        bus = CapabilityBus(str(temp_paths.workspace_dir))
        registry = CapabilityRegistry(
            workspace_dir=temp_paths.workspace_dir,
            capability_bus=bus,
            skill_marketplace=marketplace,
            app_manager=app_manager,
        )
        registry.refresh_local_index(save=True)
        grant = registry.issue_app_grant(caller_app="crm", provides="parse.doc")

        restored = CapabilityRegistry(
            workspace_dir=temp_paths.workspace_dir,
            capability_bus=CapabilityBus(str(temp_paths.workspace_dir)),
            skill_marketplace=marketplace,
            app_manager=app_manager,
        )

        grants = restored.list_grants(caller_app="crm")
        assert grant["success"] is True
        assert grants[0]["provider_app"] == "parser"
        assert grants[0]["capability_name"] == "parse.doc"

    def test_capability_registry_tool_supports_install_skill_action(self, temp_paths):
        workspace_skills = temp_paths.workspace_dir / "skills"
        _write_skill(workspace_skills, "ops_skill", "Ops helper")

        skill_registry = SkillRegistry(str(workspace_skills))
        marketplace = SkillMarketplace(str(temp_paths.workspace_dir))
        packaged = marketplace.package_skill("ops_skill")
        registry = CapabilityRegistry(
            workspace_dir=temp_paths.workspace_dir,
            capability_bus=CapabilityBus(str(temp_paths.workspace_dir)),
            skill_marketplace=marketplace,
            skill_registry=skill_registry,
        )
        tool = get_capability_registry_tools(registry)[0]

        result = json.loads(tool._run(action="install_skill", package_path=packaged["path"]))

        assert result["success"] is True
        assert result["skill_name"] == "ops_skill"

    def test_capability_registry_snapshot_includes_capability_tree(self, temp_paths):
        workspace_skills = temp_paths.workspace_dir / "skills"
        _write_skill(workspace_skills, "create_app_sop", "Guide app creation")

        skill_registry = SkillRegistry(str(workspace_skills))
        marketplace = SkillMarketplace(str(temp_paths.workspace_dir))
        app_manager = AppManager(str(temp_paths.apps_dir), project_paths=temp_paths)
        app_manager.create_app("portal", description="Portal app", mode="assistant")

        bus = CapabilityBus(str(temp_paths.workspace_dir))
        registry = CapabilityRegistry(
            workspace_dir=temp_paths.workspace_dir,
            capability_bus=bus,
            skill_marketplace=marketplace,
            skill_registry=skill_registry,
            app_manager=app_manager,
        )

        snapshot = registry.refresh_local_index(save=False)
        detail_by_name = {item["name"]: item for item in snapshot["tree"]["capability_details"]}

        assert "tree" in snapshot
        assert detail_by_name["create_app_sop"]["slot"] == "skill_strategy"
        assert detail_by_name["portal"]["top_level"] == "workflow_apps"

    def test_capability_registry_discovery_prefers_trunk_first_when_query_is_generic(self, temp_paths):
        bus = CapabilityBus(str(temp_paths.workspace_dir))
        bus.register("read_file", CapabilityLayer.TOOL, description="Read workspace files")
        bus.register("create_app", CapabilityLayer.TOOL, description="Create managed apps")
        bus.register("delegate_to_agent", CapabilityLayer.TOOL, description="Delegate work to an agent")

        registry = CapabilityRegistry(
            workspace_dir=temp_paths.workspace_dir,
            capability_bus=bus,
            skill_marketplace=SkillMarketplace(str(temp_paths.workspace_dir)),
        )

        snapshot = registry.get_registry_snapshot()
        names = [item["name"] for item in snapshot["capabilities"][:3]]

        assert names == ["read_file", "create_app", "delegate_to_agent"]
        assert snapshot["capabilities"][0]["selection_reason"] == "default trunk-first ordering"

    def test_capability_registry_discovery_uses_runtime_context_for_ordering(self, temp_paths):
        bus = CapabilityBus(str(temp_paths.workspace_dir))
        bus.register("run_workflow", CapabilityLayer.TOOL, description="Execute workflow DAGs")
        bus.register("workflow_coordinator", CapabilityLayer.AGENT, description="workflow coordinator agent")
        
        bus.share_context(
            "projected_runtime_view",
            {"route": {"recommended": {"slot": "workflow_runtime", "top_level": "workflow_apps"}}},
            source="test",
        )

        registry = CapabilityRegistry(
            workspace_dir=temp_paths.workspace_dir,
            capability_bus=bus,
            skill_marketplace=SkillMarketplace(str(temp_paths.workspace_dir)),
        )

        discovery = registry.discover(query="")
        local = discovery["local"]

        assert [item["name"] for item in local[:2]] == ["run_workflow", "workflow_coordinator"]
        assert "current slot continuity" in local[0]["selection_reason"]

    def test_capability_registry_route_returns_branch_guidance(self, temp_paths):
        bus = CapabilityBus(str(temp_paths.workspace_dir))
        bus.register("read_file", CapabilityLayer.TOOL, description="Read workspace files")
        bus.register("build_app_iteratively", CapabilityLayer.TOOL, description="Build managed apps iteratively")

        registry = CapabilityRegistry(
            workspace_dir=temp_paths.workspace_dir,
            capability_bus=bus,
            skill_marketplace=SkillMarketplace(str(temp_paths.workspace_dir)),
        )

        route = registry.route(query="build_app_iteratively")

        assert route["recommended"]["mode"] == "branch_on_demand"
        assert route["recommended"]["top_level"] == "workflow_apps"
        assert route["top_matches"][0]["name"] == "build_app_iteratively"
        assert "create_app" in [item["topic"] for item in route["route_hints"]]

    def test_capability_registry_tool_supports_route_action(self, temp_paths):
        bus = CapabilityBus(str(temp_paths.workspace_dir))
        bus.register("run_workflow", CapabilityLayer.TOOL, description="Execute workflow DAGs")
        registry = CapabilityRegistry(
            workspace_dir=temp_paths.workspace_dir,
            capability_bus=bus,
            skill_marketplace=SkillMarketplace(str(temp_paths.workspace_dir)),
        )
        tool = get_capability_registry_tools(registry)[0]

        route = json.loads(tool._run(action="route", query="run_workflow", limit=3))

        assert route["recommended"]["top_level"] == "workflow_apps"
        assert route["top_matches"][0]["name"] == "run_workflow"
        assert "workflow" in [item["topic"] for item in route["route_hints"]]

    def test_capability_registry_tool_supports_discover_action(self, temp_paths):
        bus = CapabilityBus(str(temp_paths.workspace_dir))
        bus.register("my_awesome_tool", CapabilityLayer.TOOL, description="Does awesome things")
        registry = CapabilityRegistry(
            workspace_dir=temp_paths.workspace_dir,
            capability_bus=bus,
            skill_marketplace=SkillMarketplace(str(temp_paths.workspace_dir)),
        )
        tool = get_capability_registry_tools(registry)[0]

        discovery = json.loads(tool._run(action="discover", query="awesome"))

        assert discovery["query"] == "awesome"
        assert any(item["name"] == "my_awesome_tool" for item in discovery["local"])

    def test_capability_registry_tool_supports_issue_grant_and_list_grants_action(self, temp_paths):
        app_manager = AppManager(str(temp_paths.apps_dir), project_paths=temp_paths)
        app_manager.create_app("provider_app", description="Provides capabilities", exports=["super_power"])
        app_manager.create_app("caller_app", description="Calls capabilities")
        app_manager.reload_apps()

        marketplace = SkillMarketplace(str(temp_paths.workspace_dir))
        bus = CapabilityBus(str(temp_paths.workspace_dir))
        registry = CapabilityRegistry(
            workspace_dir=temp_paths.workspace_dir,
            capability_bus=bus,
            skill_marketplace=marketplace,
            app_manager=app_manager,
        )
        registry.refresh_local_index(save=True)
        tool = get_capability_registry_tools(registry)[0]

        grant_res = json.loads(tool._run(
            action="issue_grant",
            caller_app="caller_app",
            target_app="provider_app",
            provides="super_power"
        ))

        assert grant_res["success"] is True
        assert "grant" in grant_res
        assert "token" in grant_res["grant"]

        list_res = json.loads(tool._run(
            action="list_grants",
            caller_app="caller_app"
        ))
        
        assert "grants" in list_res
        assert len(list_res["grants"]) == 1
        assert list_res["grants"][0]["capability_name"] == "super_power"

    def test_capability_registry_tool_supports_invoke_action(self, temp_paths):
        app_manager = AppManager(str(temp_paths.apps_dir), project_paths=temp_paths)
        app_manager.create_app(
            "provider_app", 
            description="Provides capabilities", 
            exports=["super_power"]
        )
        app_dir = temp_paths.apps_dir / "provider_app"
        app_dir.joinpath("api.py").write_text(
            "def super_power(payload, **kwargs):\n    return {'echo': payload}\n",
            encoding="utf-8"
        )
        
        app_manager.create_app("caller_app", description="Calls capabilities")
        app_manager.reload_apps()

        marketplace = SkillMarketplace(str(temp_paths.workspace_dir))
        bus = CapabilityBus(str(temp_paths.workspace_dir))
        registry = CapabilityRegistry(
            workspace_dir=temp_paths.workspace_dir,
            capability_bus=bus,
            skill_marketplace=marketplace,
            app_manager=app_manager,
        )
        registry.refresh_local_index(save=True)
        
        grant = registry.issue_app_grant(caller_app="caller_app", provides="super_power")
        assert grant["success"] is True

        tool = get_capability_registry_tools(registry)[0]

        invoke_res = json.loads(tool._run(
            action="invoke",
            caller_app="caller_app",
            grant_token=grant["grant"]["token"],
            invoke_action="super_power",
            invoke_payload='{"msg": "hello"}'
        ))
        
        assert invoke_res["success"] is True
        assert invoke_res["result"]["echo"]["msg"] == "hello"

    def test_capability_registry_grant_quota_exhaustion_and_ttl_expiry(self, temp_paths):
        app_manager = AppManager(str(temp_paths.apps_dir), project_paths=temp_paths)
        app_manager.create_app("provider_app", description="Provides capabilities", exports=["super_power"])
        (temp_paths.apps_dir / "provider_app" / "api.py").write_text(
            "def super_power(payload, **kwargs):\n    return {'echo': payload}\n",
            encoding="utf-8",
        )
        app_manager.create_app("caller_app", description="Calls capabilities")
        app_manager.reload_apps()

        marketplace = SkillMarketplace(str(temp_paths.workspace_dir))
        bus = CapabilityBus(str(temp_paths.workspace_dir))
        registry = CapabilityRegistry(
            workspace_dir=temp_paths.workspace_dir,
            capability_bus=bus,
            skill_marketplace=marketplace,
            app_manager=app_manager,
        )
        registry.refresh_local_index(save=True)

        grant_quota = registry.issue_app_grant(
            caller_app="caller_app", 
            provides="super_power", 
            requested_quota=1
        )
        assert grant_quota["success"] is True
        
        res1 = registry.invoke_app_capability(
            caller_app="caller_app",
            grant_token=grant_quota["grant"]["token"],
            action="super_power",
            payload={}
        )
        assert res1["success"] is True
        
        res2 = registry.invoke_app_capability(
            caller_app="caller_app",
            grant_token=grant_quota["grant"]["token"],
            action="super_power",
            payload={}
        )
        assert res2["success"] is False
        assert "quota exhausted" in res2["error"].lower()

        grant_ttl = registry.issue_app_grant(
            caller_app="caller_app", 
            provides="super_power", 
            ttl_seconds=-10
        )
        assert grant_ttl["success"] is True
        
        res3 = registry.invoke_app_capability(
            caller_app="caller_app",
            grant_token=grant_ttl["grant"]["token"],
            action="super_power",
            payload={}
        )
        assert res3["success"] is False
        assert "expired" in res3["error"].lower()


# ---------------------------------------------------------------------------
# 3. LangChain Bus Middleware Tests (formerly test_lc_bus_middleware.py)
# ---------------------------------------------------------------------------

@pytest.mark.skipif(not _HAS_LC, reason="langchain not installed")
class TestBusModelCall:
    def test_records_duration(self):
        bus = MagicMock()
        mw = LCBusMiddleware(bus)
        request = MagicMock()
        request.messages = []

        def handler(req):
            return MagicMock()

        mw.wrap_model_call(request, handler)
        assert bus.share_context.call_count == 2
        first_call = bus.share_context.call_args_list[0]
        second_call = bus.share_context.call_args_list[1]
        assert first_call[0][0] == "last_invoke_duration_ms"
        assert isinstance(first_call[0][1], float)
        assert first_call[1]["source"] == "bus_middleware"
        assert second_call[0][0] == "last_model_call"
        assert second_call[0][1]["message_count"] == 0


@pytest.mark.skipif(not _HAS_LC, reason="langchain not installed")
class TestBusToolCall:
    def test_records_tool_invocation(self):
        bus = MagicMock()
        mw = LCBusMiddleware(bus)
        request = MagicMock()
        request.name = "my_tool"
        tool_msg = ToolMessage(content="ok", tool_call_id="tc1")

        def handler(req):
            return tool_msg

        result = mw.wrap_tool_call(request, handler)
        assert result is tool_msg
        bus.record_invocation.assert_called_once()
        args = bus.record_invocation.call_args
        assert args[0][0] == "my_tool"
        assert args[1]["success"] is True
        assert args[1]["source"] == "lc_bus_middleware"
        assert args[1]["layer"] == "tool"
        assert args[1]["operation"] == "tool_call"

    def test_records_command_as_non_success(self):
        bus = MagicMock()
        mw = LCBusMiddleware(bus)
        request = MagicMock()
        request.name = "tool"
        cmd = Command(update={"messages": []})

        def handler(req):
            return cmd

        mw.wrap_tool_call(request, handler)
        args = bus.record_invocation.call_args
        assert args[1]["success"] is False
        assert args[1]["metadata"]["result_type"] == "Command"


@pytest.mark.skipif(not _HAS_LC, reason="langchain not installed")
class TestBusProperties:
    def test_name(self):
        mw = LCBusMiddleware(MagicMock())
        assert mw.name == "LCBusMiddleware"


# ---------------------------------------------------------------------------
# 4. Runtime Capability Bundle Tests (formerly test_runtime_capability_bundle.py)
# ---------------------------------------------------------------------------

class TestRuntimeCapabilityBundleClass:
    def test_build_capability_runtime_bundle_wires_shared_services(self, temp_paths):
        skill_registry = SkillRegistry(str(temp_paths.skills_dir))
        skill_marketplace = SkillMarketplace(str(temp_paths.workspace_dir))
        app_manager = AppManager(str(temp_paths.apps_dir), project_paths=temp_paths)
        agent_storage = AgentStorage(base_dir=str(temp_paths.agents_dir))

        bundle = build_capability_runtime_bundle(
            paths=temp_paths,
            thread_id="thread-1",
            summarize_callback=lambda text: f"summary:{text}",
            tool_callback=lambda tool, args: {"tool": tool, "args": args},
            agent_callback=lambda prompt: f"agent:{prompt}",
            delegate_callback=lambda agent, task, context: {"agent": agent, "task": task, "context": context},
            skill_registry=skill_registry,
            skill_marketplace=skill_marketplace,
            app_manager=app_manager,
            agent_storage=agent_storage,
            control_config={"mode": "balanced"},
        )

        assert bundle.pyflow_engine.approval_queue is bundle.approval_queue
        assert bundle.capability_registry.capability_bus is bundle.capability_bus
        assert bundle.capability_registry.catalog_runtime.pyflow_engine is bundle.pyflow_engine
        assert bundle.tool_chain._tool_callback is not None
        assert bundle.eval_framework.runtime._agent_callback is not None
        assert bundle.context_manager.config.thread_id == "thread-1"
        assert bundle.middleware_stack.layers == []


# ---------------------------------------------------------------------------
# 5. MCP Hub Tests (formerly test_mcp_hub.py)
# ---------------------------------------------------------------------------

@pytest.fixture
def mcp_tmpdir():
    with tempfile.TemporaryDirectory() as d:
        yield d


class TestMCPServerConfig:
    def test_defaults(self):
        cfg = MCPServerConfig(name="test", command="echo")
        assert cfg.transport == "stdio"
        assert cfg.enabled is True
        assert cfg.args == []
        assert cfg.env == {}


class TestMCPToolDescriptor:
    def test_fields(self):
        td = MCPToolDescriptor(name="query", description="Run SQL", server_name="sqlite")
        assert td.name == "query"
        assert td.server_name == "sqlite"


class TestMCPResourceDescriptor:
    def test_fields(self):
        rd = MCPResourceDescriptor(uri="file://test.db", name="database", description="Main DB")
        assert rd.uri == "file://test.db"


class TestMCPHubConfig:
    def test_creates_default_config(self, mcp_tmpdir):
        MCPHub(mcp_tmpdir)
        config_path = os.path.join(mcp_tmpdir, "mcp_servers.json")
        assert os.path.exists(config_path)
        with open(config_path, encoding="utf-8") as f:
            data = json.load(f)
        assert "mcpServers" in data

    def test_loads_existing_config(self, mcp_tmpdir):
        config = {
            "mcpServers": {
                "my_server": {
                    "command": "python",
                    "args": ["-m", "mcp_server"],
                    "enabled": True,
                }
            }
        }
        config_path = os.path.join(mcp_tmpdir, "mcp_servers.json")
        with open(config_path, "w", encoding="utf-8") as f:
            json.dump(config, f)

        hub = MCPHub(mcp_tmpdir)
        assert "my_server" in hub._configs
        assert hub._configs["my_server"].command == "python"

    def test_disabled_server_not_started(self, mcp_tmpdir):
        config = {
            "mcpServers": {
                "disabled_server": {
                    "command": "echo",
                    "args": ["hello"],
                    "enabled": False,
                }
            }
        }
        config_path = os.path.join(mcp_tmpdir, "mcp_servers.json")
        with open(config_path, "w", encoding="utf-8") as f:
            json.dump(config, f)

        hub = MCPHub(mcp_tmpdir)
        result = hub.start_all()
        assert result["disabled_server"] is False

    def test_get_tools_empty_when_no_servers(self, mcp_tmpdir):
        hub = MCPHub(mcp_tmpdir)
        tools = hub.get_tools()
        assert tools == []


class TestMCPHubServerStatus:
    def test_status_no_connections(self, mcp_tmpdir):
        config = {
            "mcpServers": {
                "test_server": {
                    "command": "echo",
                    "args": [],
                    "enabled": True,
                }
            }
        }
        config_path = os.path.join(mcp_tmpdir, "mcp_servers.json")
        with open(config_path, "w", encoding="utf-8") as f:
            json.dump(config, f)

        hub = MCPHub(mcp_tmpdir)
        status = hub.get_server_status()
        assert "test_server" in status
        assert status["test_server"]["configured"] is True
        assert status["test_server"]["running"] is False

    def test_start_nonexistent_server(self, mcp_tmpdir):
        hub = MCPHub(mcp_tmpdir)
        result = hub.start_server("nonexistent")
        assert result is False


class TestMCPHubToolSync:
    def test_call_tool_sync_no_server(self, mcp_tmpdir):
        hub = MCPHub(mcp_tmpdir)
        result = hub.call_tool_sync("missing", "test", {})
        assert "not running" in result

    def test_read_resource_no_server(self, mcp_tmpdir):
        hub = MCPHub(mcp_tmpdir)
        result = hub.read_resource("missing", "file://test")
        assert "not running" in result


class TestMCPToolAdapter:
    def test_adapter_name(self, mcp_tmpdir):
        hub = MCPHub(mcp_tmpdir)
        desc = MCPToolDescriptor(name="query", description="Run a query", server_name="sqlite")
        adapter = MCPToolAdapter(hub=hub, descriptor=desc)
        assert adapter.name == "mcp_sqlite_query"
        assert "query" in adapter.description.lower() or "Run a query" in adapter.description

    def test_adapter_run_returns_error_for_missing_server(self, mcp_tmpdir):
        hub = MCPHub(mcp_tmpdir)
        desc = MCPToolDescriptor(name="query", description="test", server_name="missing")
        adapter = MCPToolAdapter(hub=hub, descriptor=desc)
        result = adapter._run(sql="SELECT 1")
        assert "not running" in result


class TestMCPServerConnection:
    def test_not_running_initially(self):
        cfg = MCPServerConfig(name="test", command="nonexistent_cmd_xyz")
        conn = MCPServerConnection(cfg)
        assert conn.is_running is False

    def test_start_with_bad_command(self):
        cfg = MCPServerConfig(name="test", command="nonexistent_command_abc123")
        conn = MCPServerConnection(cfg)
        result = conn.start()
        assert result is False
        assert conn.is_running is False

    def test_tools_empty_before_start(self):
        cfg = MCPServerConfig(name="test", command="echo")
        conn = MCPServerConnection(cfg)
        assert conn.tools == []
        assert conn.resources == []


class TestMCPHubDescriptors:
    def test_get_all_tool_descriptors_empty(self, mcp_tmpdir):
        hub = MCPHub(mcp_tmpdir)
        assert hub.get_all_tool_descriptors() == []

    def test_get_all_resource_descriptors_empty(self, mcp_tmpdir):
        hub = MCPHub(mcp_tmpdir)
        assert hub.get_all_resource_descriptors() == []


class TestMCPHubLifecycle:
    def test_stop_all_no_crash(self, mcp_tmpdir):
        hub = MCPHub(mcp_tmpdir)
        hub.stop_all()

    def test_stop_nonexistent_server(self, mcp_tmpdir):
        hub = MCPHub(mcp_tmpdir)
        hub.stop_server("nope")

    def test_restart_nonexistent(self, mcp_tmpdir):
        hub = MCPHub(mcp_tmpdir)
        result = hub.restart_server("nope")
        assert result is False
