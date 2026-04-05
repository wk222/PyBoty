from __future__ import annotations

import json
from types import SimpleNamespace

from core.systems.bus.capability_bus import CapabilityBus, CapabilityLayer, get_capability_bus_tools
from core.systems.bus.capability_bus_models import EventType


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
