"""Model-hook coordination helpers for dynamic tool middleware."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from .tool_call_runtime import ToolCallRuntime

_TOOL_MUTATION_NAMES = {"create_custom_tool", "create_agent", "remove_custom_tool"}


class ToolModelHookRuntime:
    """Coordinate before/after-model hooks around dynamic tool inventory."""

    def __init__(self, *, inventory: Any, control_runtime: Any):
        self._inventory = inventory
        self._control_runtime = control_runtime

    def before_model(self) -> None:
        try:
            mutation_notice = self._inventory.pop_mutation_notice()
            if mutation_notice:
                print(f"[DynamicToolMiddleware] 工具 '{mutation_notice}' 已创建")

            dynamic_tools = self._inventory.list_dynamic_tools()
            self._inventory.refresh(dynamic_tools)

            if dynamic_tools:
                print(f"[DynamicToolMiddleware] 加载了 {len(dynamic_tools)} 个动态工具")
        except Exception as exc:
            print(f"[DynamicToolMiddleware] 加载工具时出错: {exc}")
            self._inventory.fallback_to_base_tools()

    def inject_tools(self, request: Any) -> Any:
        request, added_count = self._inventory.inject_tools(request)
        if added_count:
            print(f"[DynamicToolMiddleware] 注入了 {added_count} 个工具")
        return request

    def wrap_model_call(self, request: Any, handler: Callable[[Any], Any]) -> Any:
        try:
            request = self.inject_tools(request)
        except Exception as exc:
            print(f"[DynamicToolMiddleware] 注入工具时出错: {exc}")
        return handler(request)

    async def wrap_model_call_async(
        self,
        request: Any,
        handler: Callable[[Any], Awaitable[Any]],
    ) -> Any:
        try:
            request = self.inject_tools(request)
        except Exception as exc:
            print(f"[DynamicToolMiddleware] 注入工具时出错: {exc}")
        return await handler(request)

    def after_model(self, state: Any) -> dict[str, Any] | None:
        messages = state.get("messages", [])
        if not messages:
            return None

        last_message = messages[-1]
        tool_calls = getattr(last_message, "tool_calls", []) or []
        approval_update = self._control_runtime.interrupt_for_pending_approvals(
            last_message=last_message,
            tool_calls=tool_calls,
            dynamic_tool_names=self._inventory.get_dynamic_tool_names(),
        )
        if approval_update is not None:
            return approval_update

        for tool_call in tool_calls:
            tool_name = ToolCallRuntime.tool_name(tool_call)
            if tool_name in _TOOL_MUTATION_NAMES:
                print(f"[DynamicToolMiddleware] 检测到 {tool_name} 调用")
                break
        return None
