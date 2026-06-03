"""App branch surfaces grouped by runtime, modes, and orchestration."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from core.systems.apps.app_creator import get_app_creator_tools, set_app_manager
from core.systems.apps.app_manager import AppDefinition, AppManager, AppMode
from core.systems.apps.app_matrix_planner import (
    AppMatrixBindingProposal,
    AppMatrixPipelineProposal,
    AppMatrixPlanner,
    AppMatrixTopologyPlan,
    fallback_app_matrix_plan,
)
from core.systems.apps.app_matrix_runtime import AppMatrixRuntime
from core.systems.apps.app_orchestration import AppOrchestrationRegistry, NodeStatus, NodeType, OrchestrationNode
from core.systems.apps.app_orchestration_tools import get_app_orchestration_tools
from core.assets.apps.packager import AppPackager
from core.systems.apps.app_verifier import (
    AppVerificationService,
    ReadAppFileTool,
    VerifyAppTool,
    get_app_verifier_tools,
    set_verifier_app_manager,
)
from core.systems.apps.iterative_app_builder import IterativeAppBuilderTool
from core.systems.apps.marketplace_tools import get_app_marketplace_tools


@dataclass(frozen=True, slots=True)
class AppRuntimeSurface:
    manager_class: type[AppManager]
    definition_class: type[AppDefinition]
    mode_class: type[AppMode]
    creator_tools_factory: Callable[..., list[Any]]
    verifier_tools_factory: Callable[[], list[Any]]
    verification_service_class: type[AppVerificationService]
    iterative_builder_tool: type[IterativeAppBuilderTool]
    set_manager: Callable[[AppManager], None]
    set_verifier_manager: Callable[[AppManager], None]
    verify_tool: type[VerifyAppTool]
    read_file_tool: type[ReadAppFileTool]


@dataclass(frozen=True, slots=True)
class AppModesSurface:
    matrix_runtime_class: type[AppMatrixRuntime]
    matrix_planner_class: type[AppMatrixPlanner]
    matrix_plan_class: type[AppMatrixTopologyPlan]
    binding_proposal_class: type[AppMatrixBindingProposal]
    pipeline_proposal_class: type[AppMatrixPipelineProposal]
    packager_class: type[AppPackager]
    fallback_plan: Callable[..., Any]


@dataclass(frozen=True, slots=True)
class AppOrchestrationSurface:
    registry_class: type[AppOrchestrationRegistry]
    node_type_enum: type[NodeType]
    node_status_enum: type[NodeStatus]
    node_class: type[OrchestrationNode]
    orchestration_tools_factory: Callable[..., list[Any]]
    marketplace_tools_factory: Callable[[], list[Any]]


@dataclass(frozen=True, slots=True)
class AppBranchSurface:
    runtime: AppRuntimeSurface
    modes: AppModesSurface
    orchestration: AppOrchestrationSurface


app_runtime = AppRuntimeSurface(
    manager_class=AppManager,
    definition_class=AppDefinition,
    mode_class=AppMode,
    creator_tools_factory=get_app_creator_tools,
    verifier_tools_factory=get_app_verifier_tools,
    verification_service_class=AppVerificationService,
    iterative_builder_tool=IterativeAppBuilderTool,
    set_manager=set_app_manager,
    set_verifier_manager=set_verifier_app_manager,
    verify_tool=VerifyAppTool,
    read_file_tool=ReadAppFileTool,
)

app_modes = AppModesSurface(
    matrix_runtime_class=AppMatrixRuntime,
    matrix_planner_class=AppMatrixPlanner,
    matrix_plan_class=AppMatrixTopologyPlan,
    binding_proposal_class=AppMatrixBindingProposal,
    pipeline_proposal_class=AppMatrixPipelineProposal,
    packager_class=AppPackager,
    fallback_plan=fallback_app_matrix_plan,
)

app_orchestration = AppOrchestrationSurface(
    registry_class=AppOrchestrationRegistry,
    node_type_enum=NodeType,
    node_status_enum=NodeStatus,
    node_class=OrchestrationNode,
    orchestration_tools_factory=get_app_orchestration_tools,
    marketplace_tools_factory=get_app_marketplace_tools,
)

app_branch = AppBranchSurface(
    runtime=app_runtime,
    modes=app_modes,
    orchestration=app_orchestration,
)

__all__ = [
    "AppBranchSurface",
    "AppDefinition",
    "AppManager",
    "AppMatrixBindingProposal",
    "AppMatrixPipelineProposal",
    "AppMatrixPlanner",
    "AppMatrixRuntime",
    "AppMatrixTopologyPlan",
    "AppMode",
    "AppModesSurface",
    "AppOrchestrationRegistry",
    "AppOrchestrationSurface",
    "AppPackager",
    "AppRuntimeSurface",
    "AppVerificationService",
    "IterativeAppBuilderTool",
    "NodeStatus",
    "NodeType",
    "OrchestrationNode",
    "ReadAppFileTool",
    "VerifyAppTool",
    "app_branch",
    "app_modes",
    "app_orchestration",
    "app_runtime",
    "fallback_app_matrix_plan",
    "get_app_creator_tools",
    "get_app_marketplace_tools",
    "get_app_orchestration_tools",
    "get_app_verifier_tools",
    "set_app_manager",
    "set_verifier_app_manager",
]
