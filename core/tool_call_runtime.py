"""Execution helpers for LangChain tool-call middleware."""

from __future__ import annotations

import json
import time
from collections.abc import Awaitable, Callable
from typing import Any

from langchain_core.messages import ToolMessage
from langgraph.types import Command

from .agent_control import ToolControlDecision, ToolRiskLevel
from .tool_control_runtime import ToolControlRuntime
from .tool_delegation_runtime import DelegatedToolApprovalRuntime
from .tool_dynamic_inventory import DynamicToolInventory


class ToolCallRuntime:
    """Execute tool calls with policy enforcement and delegated-approval handling."""

    def __init__(
        self,
        *,
        inventory: DynamicToolInventory,
        control_runtime: ToolControlRuntime,
        delegated_runtime: DelegatedToolApprovalRuntime,
    ):
        self._inventory = inventory
        self._control_runtime = control_runtime
        self._delegated_runtime = delegated_runtime

    def run_tool_call(
        self,
        request: Any,
        handler: Callable[[Any], ToolMessage | Command],
    ) -> ToolMessage | Command:
        tool_call = request.tool_call
        tool_name = self.tool_name(tool_call)
        tool_args = self.tool_args(tool_call)
        tool_call_id = self.tool_call_id(tool_call)
        is_dynamic = self._inventory.is_dynamic_tool(tool_name)

        control_result = self._control_runtime.enforce_tool_call(
            tool_name=tool_name,
            tool_args=tool_args,
            tool_call_id=tool_call_id,
            is_dynamic=is_dynamic,
        )
        if control_result is not None:
            return control_result

        stuck_result = self._control_runtime.check_stuck_loop(tool_name, tool_args)
        if stuck_result is not None:
            return ToolMessage(content=stuck_result, tool_call_id=tool_call_id, status="error")

        self._increment_usage(tool_name)
        started_at = time.time()

        try:
            result = handler(request)
            return self.finalize_tool_result(
                tool_name=tool_name,
                tool_args=tool_args,
                tool_call_id=tool_call_id,
                is_dynamic=is_dynamic,
                started_at=started_at,
                result=result,
            )
        except Exception as exc:
            self._record_execution_error(
                tool_name=tool_name,
                tool_args=tool_args,
                is_dynamic=is_dynamic,
                error=exc,
            )
            raise

    async def run_tool_call_async(
        self,
        request: Any,
        handler: Callable[[Any], Awaitable[ToolMessage | Command]],
    ) -> ToolMessage | Command:
        tool_call = request.tool_call
        tool_name = self.tool_name(tool_call)
        tool_args = self.tool_args(tool_call)
        tool_call_id = self.tool_call_id(tool_call)
        is_dynamic = self._inventory.is_dynamic_tool(tool_name)

        control_result = self._control_runtime.enforce_tool_call(
            tool_name=tool_name,
            tool_args=tool_args,
            tool_call_id=tool_call_id,
            is_dynamic=is_dynamic,
        )
        if control_result is not None:
            return control_result

        stuck_result = self._control_runtime.check_stuck_loop(tool_name, tool_args)
        if stuck_result is not None:
            return ToolMessage(content=stuck_result, tool_call_id=tool_call_id, status="error")

        self._increment_usage(tool_name)
        started_at = time.time()

        try:
            result = await handler(request)
            return self.finalize_tool_result(
                tool_name=tool_name,
                tool_args=tool_args,
                tool_call_id=tool_call_id,
                is_dynamic=is_dynamic,
                started_at=started_at,
                result=result,
            )
        except Exception as exc:
            self._record_execution_error(
                tool_name=tool_name,
                tool_args=tool_args,
                is_dynamic=is_dynamic,
                error=exc,
            )
            raise

    def finalize_tool_result(
        self,
        *,
        tool_name: str,
        tool_args: dict[str, Any],
        tool_call_id: str,
        is_dynamic: bool,
        started_at: float,
        result: ToolMessage | Command,
    ) -> ToolMessage | Command:
        exec_time = time.time() - started_at
        if isinstance(result, ToolMessage):
            delegated_result = self._delegated_runtime.handle_tool_result(
                tool_name=tool_name,
                tool_call_id=tool_call_id,
                result=result,
            )
            if delegated_result is not None:
                result = delegated_result

            is_internal_error = False
            if is_dynamic and isinstance(result.content, str):
                try:
                    content_json = json.loads(result.content)
                    if isinstance(content_json, dict) and content_json.get("success") is False:
                        is_internal_error = True
                except Exception:
                    pass

            if result.status == "error" or is_internal_error:
                if is_internal_error:
                    result.status = "error"
                print(f"[DynamicToolMiddleware] 工具失败: {tool_name} ({exec_time:.2f}s)")
            else:
                print(f"[DynamicToolMiddleware] 工具成功: {tool_name} ({exec_time:.2f}s)")

            self._inventory.note_tool_mutation(tool_name=tool_name, result=result)
            self._control_runtime.log_tool_result(
                tool_name=tool_name,
                tool_args=tool_args,
                tool_call_id=tool_call_id,
                result=result,
                is_dynamic=is_dynamic,
            )

        return result

    def _increment_usage(self, tool_name: str) -> None:
        self._control_runtime.increment_usage(tool_name)
        self._inventory.increment_usage(tool_name)

    def _record_execution_error(
        self,
        *,
        tool_name: str,
        tool_args: dict[str, Any],
        is_dynamic: bool,
        error: Exception,
    ) -> None:
        self._control_runtime.record_control_event(
            tool_name=tool_name,
            tool_args=tool_args,
            decision=ToolControlDecision(
                allowed=False,
                risk_level=ToolRiskLevel.HIGH if is_dynamic else ToolRiskLevel.MEDIUM,
                reason=str(error),
                control_tags=("execution-error",),
            ),
        )
        print(f"[DynamicToolMiddleware] 工具 {tool_name} 执行出错: {error}")

    @staticmethod
    def tool_name(tool_call: Any) -> str:
        if isinstance(tool_call, dict):
            return str(tool_call.get("name", ""))
        return str(getattr(tool_call, "name", ""))

    @staticmethod
    def tool_args(tool_call: Any) -> dict[str, Any]:
        if isinstance(tool_call, dict):
            args = tool_call.get("args", {})
        else:
            args = getattr(tool_call, "args", {})
        return args if isinstance(args, dict) else {}

    @staticmethod
    def tool_call_id(tool_call: Any) -> str:
        if isinstance(tool_call, dict):
            return str(tool_call.get("id", ""))
        return str(getattr(tool_call, "id", ""))
