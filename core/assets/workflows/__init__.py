"""Workflow branch surfaces grouped by runtime, collaboration, and orchestration."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from core.assets.workflows.pyflow_engine import PyFlowEngine
from core.assets.workflows.task_queue import TaskInfo, TaskQueue, TaskStatus
from core.assets.workflows.task_scheduler import ScheduledTask, TaskScheduler
from core.assets.workflows.workflow_collaboration_runtime import WorkflowCollaborationRuntime
from core.assets.workflows.workflow_delegation_runtime import WorkflowDelegationRuntime
from core.assets.workflows.workflow_definition_runtime import WorkflowDefinitionRuntime
from core.assets.workflows.workflow_execution_runtime import WorkflowExecutionRuntime
from core.assets.workflows.workflow_exceptions import WorkflowApprovalPause
from core.assets.workflows.workflow_graph_runtime import WorkflowGraphRuntime
from core.assets.workflows.workflow_lifecycle_runtime import WorkflowLifecycleRuntime
from core.assets.workflows.workflow_models import (
    BRANCH_NODE_TYPES,
    FlowEdge,
    FlowNode,
    NodeExecutionRecord,
    NodeStatus,
    NodeType,
    WorkflowDef,
    WorkflowRunRecord,
    WorkflowStatus,
)
from core.assets.workflows.workflow_node_runtime import WorkflowNodeRuntime
from core.assets.workflows.workflow_nodes_extended import (
    run_database_query,
    run_file_read,
    run_file_write,
    run_http_request,
    run_iteration,
    run_list_operator,
    run_parameter_extractor,
    run_question_classifier,
    run_variable_assigner,
)
from core.assets.workflows.workflow_plugin import (
    dispatch_plugin,
    get_plugin,
    list_plugins,
    register_node_plugin,
    unregister_node_plugin,
)
from core.assets.workflows.workflow_registry_runtime import WorkflowRegistryRuntime
from core.assets.workflows.workflow_spec import export_workflow_spec, parse_workflow_spec, strip_workflow_runtime
from core.assets.workflows.workflow_storage import WorkflowStorage
from core.assets.workflows.workflow_tools import get_pyflow_tools


@dataclass(frozen=True, slots=True)
class WorkflowRuntimeSurface:
    engine_class: type[PyFlowEngine]
    definition_runtime: type[WorkflowDefinitionRuntime]
    execution_runtime: type[WorkflowExecutionRuntime]
    graph_runtime: type[WorkflowGraphRuntime]
    lifecycle_runtime: type[WorkflowLifecycleRuntime]
    node_runtime: type[WorkflowNodeRuntime]
    registry_runtime: type[WorkflowRegistryRuntime]
    storage_class: type[WorkflowStorage]
    tools_factory: Callable[[Any], list[Any]]
    parse_spec: Callable[..., Any]
    export_spec: Callable[..., Any]
    strip_runtime: Callable[..., Any]
    run_http_request: Callable[..., Any]
    run_question_classifier: Callable[..., Any]
    run_variable_assigner: Callable[..., Any]
    run_list_operator: Callable[..., Any]
    run_parameter_extractor: Callable[..., Any]
    run_iteration: Callable[..., Any]
    run_database_query: Callable[..., Any]
    run_file_read: Callable[..., Any]
    run_file_write: Callable[..., Any]


@dataclass(frozen=True, slots=True)
class WorkflowCollaborationSurface:
    runtime_class: type[WorkflowCollaborationRuntime]
    delegation_runtime: type[WorkflowDelegationRuntime]
    approval_pause: type[WorkflowApprovalPause]
    branch_node_types: frozenset[Any]


@dataclass(frozen=True, slots=True)
class WorkflowOrchestrationSurface:
    scheduler_class: type[TaskScheduler]
    scheduled_task_class: type[ScheduledTask]
    task_queue_class: type[TaskQueue]
    task_info_class: type[TaskInfo]
    task_status_enum: type[TaskStatus]


@dataclass(frozen=True, slots=True)
class WorkflowBranchSurface:
    runtime: WorkflowRuntimeSurface
    collaboration: WorkflowCollaborationSurface
    orchestration: WorkflowOrchestrationSurface


workflow_runtime = WorkflowRuntimeSurface(
    engine_class=PyFlowEngine,
    definition_runtime=WorkflowDefinitionRuntime,
    execution_runtime=WorkflowExecutionRuntime,
    graph_runtime=WorkflowGraphRuntime,
    lifecycle_runtime=WorkflowLifecycleRuntime,
    node_runtime=WorkflowNodeRuntime,
    registry_runtime=WorkflowRegistryRuntime,
    storage_class=WorkflowStorage,
    tools_factory=get_pyflow_tools,
    parse_spec=parse_workflow_spec,
    export_spec=export_workflow_spec,
    strip_runtime=strip_workflow_runtime,
    run_http_request=run_http_request,
    run_question_classifier=run_question_classifier,
    run_variable_assigner=run_variable_assigner,
    run_list_operator=run_list_operator,
    run_parameter_extractor=run_parameter_extractor,
    run_iteration=run_iteration,
    run_database_query=run_database_query,
    run_file_read=run_file_read,
    run_file_write=run_file_write,
)

workflow_collaboration = WorkflowCollaborationSurface(
    runtime_class=WorkflowCollaborationRuntime,
    delegation_runtime=WorkflowDelegationRuntime,
    approval_pause=WorkflowApprovalPause,
    branch_node_types=BRANCH_NODE_TYPES,
)

workflow_orchestration = WorkflowOrchestrationSurface(
    scheduler_class=TaskScheduler,
    scheduled_task_class=ScheduledTask,
    task_queue_class=TaskQueue,
    task_info_class=TaskInfo,
    task_status_enum=TaskStatus,
)

workflow_branch = WorkflowBranchSurface(
    runtime=workflow_runtime,
    collaboration=workflow_collaboration,
    orchestration=workflow_orchestration,
)

__all__ = [
    "BRANCH_NODE_TYPES",
    "FlowEdge",
    "FlowNode",
    "NodeExecutionRecord",
    "NodeStatus",
    "NodeType",
    "PyFlowEngine",
    "ScheduledTask",
    "TaskInfo",
    "TaskQueue",
    "TaskScheduler",
    "TaskStatus",
    "WorkflowApprovalPause",
    "WorkflowBranchSurface",
    "WorkflowCollaborationRuntime",
    "WorkflowCollaborationSurface",
    "WorkflowDef",
    "WorkflowDelegationRuntime",
    "WorkflowDefinitionRuntime",
    "WorkflowExecutionRuntime",
    "WorkflowGraphRuntime",
    "WorkflowLifecycleRuntime",
    "WorkflowNodeRuntime",
    "WorkflowOrchestrationSurface",
    "WorkflowRegistryRuntime",
    "WorkflowRunRecord",
    "WorkflowRuntimeSurface",
    "WorkflowStatus",
    "WorkflowStorage",
    "dispatch_plugin",
    "export_workflow_spec",
    "get_plugin",
    "get_pyflow_tools",
    "list_plugins",
    "parse_workflow_spec",
    "register_node_plugin",
    "run_database_query",
    "run_file_read",
    "run_file_write",
    "run_http_request",
    "run_iteration",
    "run_list_operator",
    "run_parameter_extractor",
    "run_question_classifier",
    "run_variable_assigner",
    "strip_workflow_runtime",
    "unregister_node_plugin",
    "workflow_branch",
    "workflow_collaboration",
    "workflow_orchestration",
    "workflow_runtime",
]
