"""E2E smoke tests for the tool -> skill -> workflow -> app execution path."""

from __future__ import annotations

from core.assets.tools import ToolStorage
from core.assets.skills.skill_registry import SkillRegistry
from core.assets.skills.skill_models import SkillDefinition
from core.assets.workflows.models import WorkflowDef, FlowNode, NodeType
from core.assets.workflows.pyflow_engine import PyFlowEngine
from core.modes.apps.app_manager import AppManager
from core.systems.bus.capability_bus import CapabilityBus
from core.systems.bus.capability_registry import CapabilityRegistry
from core.assets.skills.skill_marketplace import SkillMarketplace


def test_e2e_smoke_matrix_tool_skill_workflow_app_path(temp_paths):
    # 1. Create a Tool
    tool_storage = ToolStorage(str(temp_paths.tools_workspace_dir))
    tool_storage.upsert_tool(
        "smoke_tool",
        {
            "name": "smoke_tool",
            "description": "A smoke tool",
            "parameters": [],
            "code": "def run():\n    return 'smoke_result'",
            "dependencies": [],
            "usage_guide": "",
        }
    )

    # 2. Create a Skill
    skill_registry = SkillRegistry(str(temp_paths.skills_dir))
    skill_def = SkillDefinition(
        name="smoke_skill",
        description="A smoke skill",
        capabilities=["smoke"],
        tools=[{"name": "smoke_tool"}],
    )
    skill_registry.install_skill("smoke_skill", skill_def)
    
    # 3. Create a Workflow
    engine = PyFlowEngine(str(temp_paths.workspace_dir))
    engine.create_workflow_definition(
        "wf_smoke",
        {
            "name": "smoke-workflow",
            "nodes": [
                {
                    "id": "run",
                    "type": "exec",
                    "config": {"tool_name": "smoke_tool"}
                }
            ],
            "edges": []
        }
    )
    
    # 4. Create an App
    app_manager = AppManager(str(temp_paths.apps_dir), project_paths=temp_paths)
    app_manager.create_app(
        "smoke_app",
        description="A smoke app",
        exports=["wf_smoke"]
    )
    
    # 5. Wire them all up in the Registry
    marketplace = SkillMarketplace(str(temp_paths.workspace_dir))
    bus = CapabilityBus(str(temp_paths.workspace_dir))
    registry = CapabilityRegistry(
        workspace_dir=temp_paths.workspace_dir,
        capability_bus=bus,
        skill_marketplace=marketplace,
        skill_registry=skill_registry,
        pyflow_engine=engine,
        app_manager=app_manager,
    )
    
    from core.assets.tools.tool_creator import get_dynamic_tools
    tools = get_dynamic_tools(tool_storage)
    
    # The ultimate smoke validation: refresh the index and ensure all layers are mapped
    snapshot = registry.refresh_local_index(tools=tools, save=True)
    
    capabilities = [cap["name"] for cap in snapshot["capabilities"]]
    
    # Verify mapping across all 4 layers
    assert "smoke_tool" in capabilities
    assert "smoke_skill" in capabilities
    assert "smoke-workflow" in capabilities
    assert "smoke_app" in capabilities
    
    # Verify App export points to the Workflow
    app_def = app_manager.get_app("smoke_app")
    assert "wf_smoke" in app_def.exports
