"""Factory helpers for assembling dynamic tool middleware runtimes."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from langchain.tools import BaseTool

from core.systems.integration.plugin_manifest import PluginRegistry
from core.systems.governance.agent_control import AgentControlPolicy
from core.systems.governance.approval_queue import ApprovalQueue
from core.systems.governance.tool_policy_pipeline import build_default_tool_policy_pipeline
from core.systems.governance.tool_control_runtime import ToolControlRuntime

from .tool_call_runtime import ToolCallRuntime
from .tool_creator import get_dynamic_tools
from .tool_delegation_runtime import DelegatedToolApprovalRuntime
from .tool_dynamic_inventory import DynamicToolInventory
from .tool_middleware_observability import ToolMiddlewareObservability
from .tool_model_runtime import ToolModelHookRuntime
from .tool_storage import ToolStorage


@dataclass
class ToolMiddlewareComponents:
    """Structured runtime components used by DynamicToolMiddleware."""

    control_policy: AgentControlPolicy
    approval_queue: ApprovalQueue
    inventory: DynamicToolInventory
    control_runtime: ToolControlRuntime
    delegated_runtime: DelegatedToolApprovalRuntime
    tool_call_runtime: ToolCallRuntime
    model_runtime: ToolModelHookRuntime


def build_tool_middleware_components(
    tool_storage: ToolStorage | None = None,
    *,
    control_policy: AgentControlPolicy | None = None,
    approval_queue: ApprovalQueue | None = None,
    approval_scope: str = "root",
    stuck_loop_threshold: int | None = None,
    stuck_loop_kill_threshold: int | None = None,
    allowed_path_roots: list[str] | tuple[str, ...] | None = None,
    plugin_registry: PluginRegistry | None = None,
    event_context_resolver: Any | None = None,
) -> ToolMiddlewareComponents:
    """Assemble the shared runtimes behind DynamicToolMiddleware."""

    resolved_policy = control_policy or AgentControlPolicy()
    resolved_queue = approval_queue or ApprovalQueue()
    stuck_warning = (
        stuck_loop_threshold if stuck_loop_threshold is not None else resolved_policy.stuck_loop_warning_threshold
    )
    stuck_kill = (
        stuck_loop_kill_threshold
        if stuck_loop_kill_threshold is not None
        else resolved_policy.stuck_loop_kill_threshold
    )
    observability = ToolMiddlewareObservability(
        max_recent_calls=resolved_policy.max_recent_tool_calls,
        stuck_loop_threshold=stuck_warning,
        stuck_loop_kill_threshold=stuck_kill,
    )
    inventory = DynamicToolInventory(tool_storage=tool_storage)
    control_runtime = ToolControlRuntime(
        control_policy=resolved_policy,
        approval_scope=approval_scope,
        observability=observability,
        policy_pipeline=build_default_tool_policy_pipeline(
            control_policy=resolved_policy,
            allowed_roots=allowed_path_roots,
            max_calls_per_tool=resolved_policy.max_recent_tool_calls,
        ),
    )
    delegated_runtime = DelegatedToolApprovalRuntime(
        approval_queue=resolved_queue,
        approval_scope=approval_scope,
    )
    tool_call_runtime = ToolCallRuntime(
        inventory=inventory,
        control_runtime=control_runtime,
        delegated_runtime=delegated_runtime,
        plugin_registry=plugin_registry,
        event_context_resolver=event_context_resolver,
    )
    model_runtime = ToolModelHookRuntime(
        inventory=inventory,
        control_runtime=control_runtime,
    )
    return ToolMiddlewareComponents(
        control_policy=resolved_policy,
        approval_queue=resolved_queue,
        inventory=inventory,
        control_runtime=control_runtime,
        delegated_runtime=delegated_runtime,
        tool_call_runtime=tool_call_runtime,
        model_runtime=model_runtime,
    )


def create_tool_middleware(
    tool_storage: ToolStorage | None = None,
    *,
    control_policy: AgentControlPolicy | None = None,
    approval_queue: ApprovalQueue | None = None,
    approval_scope: str = "root",
    allowed_path_roots: list[str] | tuple[str, ...] | None = None,
    plugin_registry: PluginRegistry | None = None,
    event_context_resolver: Any | None = None,
):
    """Build the LangChain middleware object with the shared runtime bundle."""

    from .tool_middleware import LANGCHAIN_1_AVAILABLE, DynamicToolMiddleware

    if not LANGCHAIN_1_AVAILABLE:
        raise ImportError(
            "需要 LangChain 1.0+ 才能使用中间件功能。请安装：pip install langchain>=1.0.0 langgraph>=0.2.0"
        )
    return DynamicToolMiddleware(
        tool_storage=tool_storage,
        control_policy=control_policy,
        approval_queue=approval_queue,
        approval_scope=approval_scope,
        allowed_path_roots=allowed_path_roots,
        plugin_registry=plugin_registry,
        event_context_resolver=event_context_resolver,
    )


def create_decorator_middleware(tool_storage: ToolStorage):
    """Build lightweight decorator-style middleware helpers for LangChain."""

    from langchain.agents.middleware import after_model, before_model

    @before_model
    def load_tools_middleware(state: Any, runtime: Any) -> dict[str, Any] | None:
        tools = get_dynamic_tools(tool_storage)
        if tools:
            print(f"[装饰器中间件] 加载了 {len(tools)} 个工具")
        return None

    @after_model
    def detect_tool_creation_middleware(state: Any, runtime: Any) -> dict[str, Any] | None:
        messages = state.get("messages", [])
        if not messages:
            return None
        last_msg = messages[-1]
        tool_calls = getattr(last_msg, "tool_calls", []) or []
        for tool_call in tool_calls:
            tool_name = ToolCallRuntime.tool_name(tool_call)
            if tool_name == "create_custom_tool":
                print("[装饰器中间件] 检测到工具创建")
                break
        return None

    return [load_tools_middleware, detect_tool_creation_middleware]


def list_dynamic_tool_names(tools: list[BaseTool]) -> list[str]:
    """Small helper for tests and debug surfaces."""

    return [tool.name for tool in tools]
