"""LangChain middleware for dynamic tools, policy enforcement, and approvals."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Sequence
from typing import Any

from langchain.tools import BaseTool
from langchain_core.messages import ToolMessage
from langgraph.types import Command

try:
    from langchain.agents.middleware import AgentMiddleware, AgentState, ModelRequest, ModelResponse, hook_config
    from langchain.agents.middleware.types import ToolCallRequest
    from langgraph.runtime import Runtime
    from langgraph.typing import ContextT

    LANGCHAIN_1_AVAILABLE = True
except ImportError:
    LANGCHAIN_1_AVAILABLE = False
    AgentMiddleware = object
    AgentState = dict[str, Any]
    ModelRequest = object
    ModelResponse = object
    ToolCallRequest = object
    Runtime = object
    ContextT = object

    def hook_config(**kwargs):
        def decorator(func):
            return func

        return decorator


from .agent_control import AgentControlPolicy
from .approval_queue import ApprovalQueue
from .tool_middleware_factory import build_tool_middleware_components
from .tool_storage import ToolStorage


class DynamicToolMiddleware(AgentMiddleware if LANGCHAIN_1_AVAILABLE else object):
    """Inject dynamic tools and enforce explicit control policy at tool-call time."""

    def __init__(
        self,
        tool_storage: ToolStorage | None = None,
        *,
        control_policy: AgentControlPolicy | None = None,
        approval_queue: ApprovalQueue | None = None,
        approval_scope: str = "root",
        stuck_loop_threshold: int | None = None,
        stuck_loop_kill_threshold: int | None = None,
    ):
        components = build_tool_middleware_components(
            tool_storage=tool_storage,
            control_policy=control_policy,
            approval_queue=approval_queue,
            approval_scope=approval_scope,
            stuck_loop_threshold=stuck_loop_threshold,
            stuck_loop_kill_threshold=stuck_loop_kill_threshold,
        )
        self.control_policy = components.control_policy
        self.approval_queue = components.approval_queue
        self.approval_scope = approval_scope
        self._inventory = components.inventory
        self._control_runtime = components.control_runtime
        self._tool_call_runtime = components.tool_call_runtime
        self._model_runtime = components.model_runtime

    @property
    def name(self) -> str:
        return "DynamicToolMiddleware"

    @property
    def tools(self) -> list[BaseTool]:
        return self._inventory.list_dynamic_tools()

    @property
    def last_created_tool(self) -> str | None:
        return self._inventory.last_created_tool

    def set_base_tools(self, tools: Sequence[BaseTool]) -> None:
        self._inventory.set_base_tools(tools)

    def set_known_dynamic_tools(self, tool_names: Sequence[str]) -> None:
        self._inventory.set_known_dynamic_tools(tool_names)

    def get_all_tools(self) -> list[BaseTool]:
        return self._inventory.get_all_tools()

    def get_usage_stats(self) -> dict[str, int]:
        return self._control_runtime.get_usage_stats()

    def reset_usage_stats(self) -> None:
        self._control_runtime.reset_usage_stats()

    def get_stuck_loop_stats(self) -> dict[str, Any]:
        return self._control_runtime.get_stuck_loop_stats()

    def get_control_snapshot(self) -> dict[str, Any]:
        return self._control_runtime.build_snapshot(
            known_tools=[tool.name for tool in self.get_all_tools()],
            approval_queue=self.approval_queue.get_snapshot(),
        )

    @hook_config(can_jump_to=["end", "model"])
    def before_model(
        self,
        state: AgentState,
        runtime: Runtime[ContextT],
    ) -> dict[str, Any] | None:
        self._model_runtime.before_model()
        return None

    async def abefore_model(
        self,
        state: AgentState,
        runtime: Runtime[ContextT],
    ) -> dict[str, Any] | None:
        return self.before_model(state, runtime)

    def wrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], ModelResponse],
    ) -> ModelResponse:
        return self._model_runtime.wrap_model_call(request, handler)

    async def awrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], Awaitable[ModelResponse]],
    ) -> ModelResponse:
        return await self._model_runtime.wrap_model_call_async(request, handler)

    def wrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], ToolMessage | Command],
    ) -> ToolMessage | Command:
        return self._tool_call_runtime.run_tool_call(request, handler)

    async def awrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], Awaitable[ToolMessage | Command]],
    ) -> ToolMessage | Command:
        return await self._tool_call_runtime.run_tool_call_async(request, handler)

    def after_model(
        self,
        state: AgentState,
        runtime: Runtime[ContextT],
    ) -> dict[str, Any] | None:
        return self._model_runtime.after_model(state)

    async def aafter_model(
        self,
        state: AgentState,
        runtime: Runtime[ContextT],
    ) -> dict[str, Any] | None:
        return self.after_model(state, runtime)
