from __future__ import annotations

import json

from core.assets.apps.app_manager import AppManager
from core.assets.skills import SkillRegistry
from core.assets.skills.skill_marketplace import SkillMarketplace
from core.systems.bus.capability_bus import CapabilityBus, CapabilityLayer
from core.systems.bus.capability_registry import CapabilityRegistry, get_capability_registry_tools


def _write_skill(root, name: str, description: str) -> None:
    skill_dir = root / name
    skill_dir.mkdir(parents=True, exist_ok=True)
    skill_dir.joinpath("SKILL.md").write_text(
        f"---\nname: {name}\ndescription: {description}\n---\n\nUse this skill.\n",
        encoding="utf-8",
    )


def test_capability_registry_discovers_local_and_marketplace_entries(temp_paths):
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


def test_capability_registry_contract_and_publish(temp_paths, monkeypatch):
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

    monkeypatch.setattr("core.systems.bus.capability_registry.event_bus.emit", _emit)

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


def test_capability_registry_persists_grants_across_restart(temp_paths):
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


def test_capability_registry_tool_supports_install_skill_action(temp_paths):
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


def test_capability_registry_snapshot_includes_capability_tree(temp_paths):
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


def test_capability_registry_discovery_prefers_trunk_first_when_query_is_generic(temp_paths):
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


def test_capability_registry_discovery_uses_runtime_context_for_ordering(temp_paths):
    bus = CapabilityBus(str(temp_paths.workspace_dir))
    bus.register("run_workflow", CapabilityLayer.TOOL, description="Execute workflow DAGs")
    bus.register("workflow_coordinator", CapabilityLayer.AGENT, description="workflow coordinator agent")
    
    # Must nest recommended under route -> recommended as well as mock what it needs.
    # The coerce_projected_runtime_view logic will try to read route: {"recommended": ...}
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


def test_capability_registry_route_returns_branch_guidance(temp_paths):
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


def test_capability_registry_tool_supports_route_action(temp_paths):
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
