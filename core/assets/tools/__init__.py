"""Tool asset domain — Layer 2 (Second Branch) of PyBot's tree.

Provides tool creation, storage, runtime execution, templates, middleware,
risk classification, file-system / bash / diff / clarification tools,
and the unified asset inventory that feeds the capability bus.

Depends on Layer 0 (runtime/session/context) and Layer 1 (governance/memory/bus).
"""

from __future__ import annotations

from importlib import import_module
from typing import Any

_EXPORTS: dict[str, tuple[str, str]] = {
    # tool_storage — persistent tool definitions
    "ToolStorage": (".tool_storage", "ToolStorage"),
    "ToolContext": (".tool_storage", "ToolContext"),
    # tool_runtime — dynamic tool execution
    "build_input_model": (".tool_runtime", "build_input_model"),
    "render_tool_script": (".tool_runtime", "render_tool_script"),
    "execute_tool_script": (".tool_runtime", "execute_tool_script"),
    "build_dynamic_tool": (".tool_runtime", "build_dynamic_tool"),
    # tool_creator — creation and template tools
    "ToolCreatorTool": (".tool_creator", "ToolCreatorTool"),
    "TemplateToolCreator": (".tool_creator", "TemplateToolCreator"),
    "create_dynamic_tool": (".tool_creator", "create_dynamic_tool"),
    "get_tool_creator_tools": (".tool_creator", "get_tool_creator_tools"),
    "get_dynamic_tools": (".tool_creator", "get_dynamic_tools"),
    # tool_creation_support — validation and compilation
    "ToolCreationError": (".tool_creation_support", "ToolCreationError"),
    "ToolTarget": (".tool_creation_support", "ToolTarget"),
    "validate_tool_name": (".tool_creation_support", "validate_tool_name"),
    "build_tool_definition": (".tool_creation_support", "build_tool_definition"),
    "persist_validated_tool_definition": (".tool_creation_support", "persist_validated_tool_definition"),
    # tool_templates
    "get_template_prompt_section": (".tool_templates", "get_template_prompt_section"),
    "list_templates": (".tool_templates", "list_templates"),
    # tool_chain — sequential tool execution
    "ToolChainExecutor": (".tool_chain", "ToolChainExecutor"),
    "get_tool_chain_tools": (".tool_chain", "get_tool_chain_tools"),
    # tool_middleware — dynamic tool middleware
    "DynamicToolMiddleware": (".tool_middleware", "DynamicToolMiddleware"),
    # tool_middleware_factory — middleware assembly
    "ToolMiddlewareComponents": (".tool_middleware_factory", "ToolMiddlewareComponents"),
    "build_tool_middleware_components": (".tool_middleware_factory", "build_tool_middleware_components"),
    "create_tool_middleware": (".tool_middleware_factory", "create_tool_middleware"),
    # tool_middleware_observability
    "ToolMiddlewareObservability": (".tool_middleware_observability", "ToolMiddlewareObservability"),
    # tool_eviction_middleware
    "LCToolEvictionMiddleware": (".tool_eviction_middleware", "LCToolEvictionMiddleware"),
    # tool_arg_repair
    "repair_tool_args": (".tool_arg_repair", "repair_tool_args"),
    "ToolArgRepairMiddleware": (".tool_arg_repair_middleware", "ToolArgRepairMiddleware"),
    # tool_call_runtime
    "ToolCallRuntime": (".tool_call_runtime", "ToolCallRuntime"),
    # tool_delegation_runtime
    "DelegatedToolApprovalRuntime": (".tool_delegation_runtime", "DelegatedToolApprovalRuntime"),
    # tool_model_runtime
    "ToolModelHookRuntime": (".tool_model_runtime", "ToolModelHookRuntime"),
    # tool_dynamic_inventory
    "DynamicToolInventory": (".tool_dynamic_inventory", "DynamicToolInventory"),
    # tool_cache
    "ToolCache": (".tool_cache", "ToolCache"),
    "cached_tool_call": (".tool_cache", "cached_tool_call"),
    # tool_risk — risk classification
    "RiskLevel": (".tool_risk", "RiskLevel"),
    "ToolRiskEntry": (".tool_risk", "ToolRiskEntry"),
    "ToolRiskRegistry": (".tool_risk", "ToolRiskRegistry"),
    "get_tool_risk_registry": (".tool_risk", "get_tool_risk_registry"),
    # tool_result_normalize
    "normalize_for_app_tool_proxy": (".tool_result_normalize", "normalize_for_app_tool_proxy"),
    # unified_tool_info / inventory
    "UnifiedToolInfo": (".unified_tool_info", "UnifiedToolInfo"),
    "UnifiedAssetInventory": (".unified_tool_inventory", "UnifiedAssetInventory"),
    # todo_write
    "TodoWriteTool": (".todo_write", "TodoWriteTool"),
    "TodoItem": (".todo_write", "TodoItem"),
    "TodoStatus": (".todo_write", "TodoStatus"),
    # session_notes
    "get_session_note_tools": (".session_notes_tool", "get_session_note_tools"),
    # file_system_tools
    "get_file_system_tools": (".file_system_tools", "get_file_system_tools"),
    # bash_tool
    "BashTool": (".bash_tool", "BashTool"),
    # permission_tools
    "get_permission_tools": (".permission_tools", "get_permission_tools"),
    # web_fetch_tool
    "WebFetchTool": (".web_fetch_tool", "WebFetchTool"),
    # llm_task_tool
    "LLMTaskTool": (".llm_task_tool", "LLMTaskTool"),
    "run_llm_task": (".llm_task_tool", "run_llm_task"),
    # clarification_tool
    "get_clarification_tools": (".clarification_tool", "get_clarification_tools"),
    # diff_tool
    "get_diff_tools": (".diff_tool", "get_diff_tools"),
}

__all__ = sorted(_EXPORTS)


def __getattr__(name: str) -> Any:
    try:
        module_name, attr_name = _EXPORTS[name]
    except KeyError as exc:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}") from exc
    module = import_module(module_name, __name__)
    return getattr(module, attr_name)


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(__all__))
