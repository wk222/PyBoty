from __future__ import annotations

from core.modes.system_model import build_system_model, get_root_mode_label, normalize_root_mode


def test_system_model_exposes_canonical_layers():
    model = build_system_model()

    assert model["root_mode_progression"] == ["assistant", "app_matrix", "admin"]
    assert [item["name"] for item in model["interaction_surfaces"]] == [
        "chat",
        "governance",
        "ecosystem",
    ]
    assert [item["name"] for item in model["ecosystem_families"]] == [
        "apps",
        "workflows",
        "skills",
        "tools",
        "agents",
    ]
    assert [item["name"] for item in model["product_concepts"]] == [
        "tools",
        "skills",
        "agents",
        "workflows",
        "apps",
    ]
    assert {item["name"] for item in model["supporting_systems"]} == {
        "runtime_foundation",
        "knowledge_and_memory",
        "governance_and_safety",
        "delivery_and_integration",
    }
    root_modes = {item["name"]: item for item in model["root_modes"]}
    assert "interactive_chat" in root_modes["assistant"]["enabled_capabilities"]
    assert "app_orchestration" in root_modes["app_matrix"]["enabled_capabilities"]
    assert "durable_goal_loop" in root_modes["admin"]["enabled_capabilities"]
    assert {item["path"] for item in model["package_targets"]} >= {
        "core/modes/",
        "core/assets/tools/",
        "core/assets/skills/",
        "core/assets/agents/",
        "core/assets/workflows/",
        "core/assets/apps/",
        "core/systems/runtime/",
        "core/systems/memory/",
        "core/systems/governance/",
        "core/systems/integration/",
    }
    assert "MCP" in model["not_product_concepts"]
    assert model["mode_profiles_modular"] is True
    assert any(rule.startswith("一级交互入口默认只有三个") for rule in model["canonical_rules"])


def test_asset_packages_expose_apps_and_workflows_entrypoints():
    from core.assets.agents import AgentStorage
    from core.assets.apps.app_manager import AppManager
    from core.assets.apps.app_matrix_runtime import AppMatrixRuntime
    from core.assets.tools import ToolStorage
    from core.assets.workflows.engine import PyFlowEngine
    from core.assets.workflows.execution import WorkflowExecutionRuntime
    from core.assets.workflows.scheduling import ScheduledTask, TaskQueue
    from core.systems.governance import ApprovalQueue
    from core.systems.memory import MemoryManager
    from core.systems.runtime import ProjectPaths

    assert AppManager is not None
    assert AppMatrixRuntime is not None
    assert AgentStorage is not None
    assert ToolStorage is not None
    assert ProjectPaths is not None
    assert ApprovalQueue is not None
    assert MemoryManager is not None
    assert WorkflowExecutionRuntime is not None
    assert PyFlowEngine is not None
    assert TaskQueue is not None
    assert ScheduledTask is not None


def test_normalize_root_mode_uses_canonical_aliases():
    assert normalize_root_mode("admin") == "admin"
    assert normalize_root_mode("矩阵管家") == "app_matrix"
    assert normalize_root_mode("unknown-mode") == "assistant"
    assert get_root_mode_label("admin agent") == "全局管理员智能体"
