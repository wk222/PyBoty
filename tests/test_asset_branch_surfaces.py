from __future__ import annotations

from core.modes.apps import app_branch, app_modes, app_orchestration, app_runtime
from core.assets.workflows import workflow_branch, workflow_collaboration, workflow_orchestration, workflow_runtime


def test_workflow_branch_surfaces_are_grouped_by_layer():
    assert workflow_branch.runtime is workflow_runtime
    assert workflow_branch.collaboration is workflow_collaboration
    assert workflow_branch.orchestration is workflow_orchestration
    assert workflow_runtime.engine_class.__name__ == "PyFlowEngine"
    assert workflow_collaboration.runtime_class.__name__ == "WorkflowCollaborationRuntime"
    assert workflow_orchestration.scheduler_class.__name__ == "TaskScheduler"


def test_workflow_runtime_surface_exports_extended_node_operators():
    assert callable(workflow_runtime.run_http_request)
    assert callable(workflow_runtime.run_question_classifier)
    assert callable(workflow_runtime.run_database_query)
    assert callable(workflow_runtime.run_file_write)


def test_app_branch_surfaces_are_grouped_by_layer():
    assert app_branch.runtime is app_runtime
    assert app_branch.modes is app_modes
    assert app_branch.orchestration is app_orchestration
    assert app_runtime.manager_class.__name__ == "AppManager"
    assert app_modes.matrix_runtime_class.__name__ == "AppMatrixRuntime"
    assert app_orchestration.registry_class.__name__ == "AppOrchestrationRegistry"


def test_app_runtime_surface_keeps_managed_entrypoints():
    tool_names = {tool.name for tool in app_runtime.creator_tools_factory()}
    assert "create_app" in tool_names
    assert "update_app_file" in tool_names
    assert "test_app_api" in tool_names
