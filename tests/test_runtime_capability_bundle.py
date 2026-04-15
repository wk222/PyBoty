from __future__ import annotations

from core.modes.agents.agent_storage import AgentStorage
from core.modes.apps.app_manager import AppManager
from core.assets.skills.skill_marketplace import SkillMarketplace
from core.assets.skills.skill_registry import SkillRegistry
from core.systems.runtime.runtime_capability_bundle import build_capability_runtime_bundle


def test_build_capability_runtime_bundle_wires_shared_services(temp_paths):
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

