from __future__ import annotations

from core.assets.apps.manager import AppManager
from core.assets.skills import SkillRegistry
from core.assets.skills.skill_marketplace import SkillMarketplace
from core.systems.bus.capability_bus import CapabilityBus, CapabilityLayer
from core.systems.bus.capability_registry import CapabilityRegistry


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
    assert result["success"] is True
    assert any(event_type == "capability_published" for event_type, _ in published_events)
