"""Execution helpers for LangChain tool-call middleware."""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Awaitable, Callable
from typing import Any

from langchain_core.messages import ToolMessage
from langgraph.types import Command

try:
    from langgraph.errors import GraphInterrupt as _GraphInterrupt
except ImportError:
    _GraphInterrupt = None  # type: ignore[assignment,misc]

from core.systems.runtime.errors import ToolRateLimitError, ToolTimeoutError, format_error
from core.systems.integration.plugin_manifest import PluginRegistry, get_plugin_registry
from core.plugin_sdk import ToolCallHookContext
from core.systems.runtime.retry_policy import RetryConfig, RetryPolicy
from core.systems.governance.agent_control import ToolControlDecision, ToolRiskLevel
from core.systems.governance.tool_control_runtime import ToolControlRuntime

from .tool_arg_repair import repair_tool_args
from .tool_delegation_runtime import DelegatedToolApprovalRuntime
from .tool_dynamic_inventory import DynamicToolInventory
from .tool_result_normalize import canonicalize_dynamic_tool_content_string

_RETRYABLE_EXCEPTIONS = (ConnectionError, TimeoutError, OSError, ToolTimeoutError)

logger = logging.getLogger(__name__)


def _preview_tool_payload(value: str, *, limit: int = 240) -> str:
    normalized = " ".join(str(value).split())
    if len(normalized) <= limit:
        return normalized
    return f"{normalized[: limit - 1]}…"


class ToolCallRuntime:
    """Execute tool calls with policy enforcement and delegated-approval handling."""

    def __init__(
        self,
        *,
        inventory: DynamicToolInventory,
        control_runtime: ToolControlRuntime,
        delegated_runtime: DelegatedToolApprovalRuntime,
        retry_policy: RetryPolicy | None = None,
        per_tool_retry: dict[str, RetryPolicy] | None = None,
        plugin_registry: PluginRegistry | None = None,
        event_context_resolver: Callable[[], dict[str, Any]] | None = None,
    ):
        self._inventory = inventory
        self._control_runtime = control_runtime
        self._delegated_runtime = delegated_runtime
        self._plugin_registry = plugin_registry or get_plugin_registry()
        self._event_context_resolver = event_context_resolver
        self._default_retry = retry_policy or RetryPolicy(
            config=RetryConfig(max_attempts=2, base_delay_seconds=0.5, max_delay_seconds=5.0),
            should_retry=lambda exc: isinstance(exc, _RETRYABLE_EXCEPTIONS),
            on_retry=lambda info: logger.warning(
                "[ToolCallRuntime] %s: retry %d/%d — %s",
                info.label,
                info.attempt,
                info.max_attempts - 1,
                type(info.error).__name__,
            ),
        )
        self._per_tool_retry: dict[str, RetryPolicy] = per_tool_retry or {}

    def _get_retry_policy(self, tool_name: str) -> RetryPolicy:
        return self._per_tool_retry.get(tool_name, self._default_retry)

    def _event_context(self) -> dict[str, Any]:
        if self._event_context_resolver is None:
            return {}
        try:
            payload = self._event_context_resolver()
        except Exception:
            return {}
        return payload if isinstance(payload, dict) else {}

    def _emit_tool_event(
        self,
        *,
        event_type: str,
        tool_name: str,
        tool_args: dict[str, Any],
        tool_call_id: str,
        status: str,
        is_dynamic: bool,
        preview: str = "",
        exec_time: float | None = None,
        error: str = "",
    ) -> None:
        from core.systems.runtime.event_bus import Event, EventType, event_bus

        context = self._event_context()
        payload = {
            "tool_name": tool_name,
            "tool_call_id": tool_call_id,
            "status": status,
            "is_dynamic": is_dynamic,
            "preview": _preview_tool_payload(preview or json.dumps(tool_args, ensure_ascii=False, default=str)),
            "args": dict(tool_args),
            "thread_id": str(context.get("thread_id", "")).strip(),
            "run_id": str(context.get("run_id", "")).strip(),
            "agent_name": str(context.get("current_agent_name", context.get("agent_name", ""))).strip(),
            "approval_scope": str(context.get("approval_scope", "")).strip(),
            "root_mode": str(context.get("root_mode", "")).strip(),
        }
        if exec_time is not None:
            payload["duration_ms"] = int(exec_time * 1000)
        if error:
            payload["error"] = error
            payload["success"] = False
        elif event_type == EventType.TOOL_RESULT.value:
            payload["success"] = status != "error"

        event_bus.emit(
            Event(
                type=EventType(event_type),
                source=f"Tool:{tool_name}",
                session_id=payload["thread_id"] or None,
                payload=payload,
            )
        )

    def _apply_execution_options(
        self,
        tool_name: str,
        tool_args: dict[str, Any],
    ) -> dict[str, Any]:
        """Modify tool arguments based on the subagent's execution options (cwd, worktree_dir)."""
        import os
        context = self._event_context()
        options = context.get("execution_options", {})
        if not options:
            return tool_args

        base_cwd = options.get("cwd", "")
        if not base_cwd:
            return tool_args

        # 1. Rebase 'cwd' if present (e.g. for bash tool)
        if "cwd" in tool_args:
            requested_cwd = str(tool_args["cwd"])
            if not os.path.isabs(requested_cwd):
                # Rebase relative CWD to the execution_options.cwd
                tool_args["cwd"] = os.path.abspath(os.path.join(base_cwd, requested_cwd))
            
        # 2. Rebase 'path' if present (e.g. for file tools)
        if "path" in tool_args:
            requested_path = str(tool_args["path"])
            if not os.path.isabs(requested_path):
                # Rebase relative path to the execution_options.cwd
                tool_args["path"] = os.path.abspath(os.path.join(base_cwd, requested_path))

        return tool_args

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

        # Apply execution options (cwd, etc) before repair and hooks
        tool_args = self._apply_execution_options(tool_name, tool_args)
        self._set_tool_args(tool_call, tool_args)

        request = self._try_repair_args(request, tool_call, tool_name, tool_args)
        tool_args = self.tool_args(request.tool_call)
        request, tool_args, plugin_block = self._run_before_tool_hooks(
            request=request,
            tool_name=tool_name,
            tool_args=tool_args,
            tool_call_id=tool_call_id,
            is_dynamic=is_dynamic,
        )
        if plugin_block is not None:
            return plugin_block

        self._emit_tool_event(
            event_type="tool_call",
            tool_name=tool_name,
            tool_args=tool_args,
            tool_call_id=tool_call_id,
            status="started",
            is_dynamic=is_dynamic,
        )

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
            result = self._get_retry_policy(tool_name).execute(
                handler,
                request,
                label=f"tool:{tool_name}",
            )
            return self.finalize_tool_result(
                tool_name=tool_name,
                tool_args=tool_args,
                tool_call_id=tool_call_id,
                is_dynamic=is_dynamic,
                started_at=started_at,
                result=result,
            )
        except Exception as exc:
            if _GraphInterrupt is not None and isinstance(exc, _GraphInterrupt):
                raise
            self._record_execution_error(
                tool_name=tool_name,
                tool_args=tool_args,
                tool_call_id=tool_call_id,
                is_dynamic=is_dynamic,
                error=exc,
            )
            return self._error_to_tool_message(exc, tool_name, tool_call_id)

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

        # Apply execution options (cwd, etc) before repair and hooks
        tool_args = self._apply_execution_options(tool_name, tool_args)
        self._set_tool_args(tool_call, tool_args)

        request = self._try_repair_args(request, tool_call, tool_name, tool_args)
        tool_args = self.tool_args(request.tool_call)
        request, tool_args, plugin_block = self._run_before_tool_hooks(
            request=request,
            tool_name=tool_name,
            tool_args=tool_args,
            tool_call_id=tool_call_id,
            is_dynamic=is_dynamic,
        )
        if plugin_block is not None:
            return plugin_block

        self._emit_tool_event(
            event_type="tool_call",
            tool_name=tool_name,
            tool_args=tool_args,
            tool_call_id=tool_call_id,
            status="started",
            is_dynamic=is_dynamic,
        )

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
            if _GraphInterrupt is not None and isinstance(exc, _GraphInterrupt):
                raise
            self._record_execution_error(
                tool_name=tool_name,
                tool_args=tool_args,
                tool_call_id=tool_call_id,
                is_dynamic=is_dynamic,
                error=exc,
            )
            return self._error_to_tool_message(exc, tool_name, tool_call_id)

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

            if is_dynamic and isinstance(result.content, str):
                try:
                    new_content = canonicalize_dynamic_tool_content_string(result.content)
                    if new_content != result.content and hasattr(result, "model_copy"):
                        result = result.model_copy(update={"content": new_content})
                    elif new_content != result.content:
                        result = ToolMessage(
                            content=new_content,
                            tool_call_id=result.tool_call_id,
                            name=getattr(result, "name", None),
                            status=getattr(result, "status", "success") or "success",
                        )
                except Exception as exc:
                    logger.debug("[ToolCallRuntime] canonicalize dynamic tool content failed: %s", exc)

            is_internal_error = False
            content_json: dict[str, Any] | None = None
            if is_dynamic and isinstance(result.content, str):
                try:
                    parsed = json.loads(result.content)
                    if isinstance(parsed, dict):
                        content_json = parsed
                        if parsed.get("success") is False:
                            is_internal_error = True
                except Exception:
                    pass

            if result.status == "error" or is_internal_error:
                if is_internal_error:
                    result.status = "error"
                    if content_json is not None:
                        self._emit_dynamic_tool_semantic_failure(tool_name, content_json)
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
            self._emit_tool_event(
                event_type="tool_result",
                tool_name=tool_name,
                tool_args=tool_args,
                tool_call_id=tool_call_id,
                status=getattr(result, "status", "success") or "success",
                is_dynamic=is_dynamic,
                preview=str(result.content),
                exec_time=exec_time,
            )
        result = self._run_after_tool_hooks(
            tool_name=tool_name,
            tool_args=tool_args,
            tool_call_id=tool_call_id,
            result=result,
            exec_time=exec_time,
            is_dynamic=is_dynamic,
        )

        if isinstance(result, ToolMessage) and result.status != "error" and self._inventory.is_return_direct(tool_name):
            return Command(goto="end", update={"messages": [result]})

        return result

    def _try_repair_args(
        self,
        request: Any,
        tool_call: Any,
        tool_name: str,
        tool_args: dict[str, Any],
    ) -> Any:
        """Auto-repair tool arguments before Pydantic validation.

        If the LLM passes a list where a str is expected (or vice versa),
        coerce the arguments to match the tool's schema. This prevents
        ValidationError from surfacing as user-facing errors.
        """
        try:
            current_tools = getattr(self._inventory, "_current_tools", [])
            if not current_tools:
                return request

            repaired_args = repair_tool_args(tool_name, tool_args, current_tools)
            if repaired_args is not tool_args:
                if isinstance(tool_call, dict):
                    tool_call["args"] = repaired_args
                elif hasattr(tool_call, "args"):
                    tool_call.args = repaired_args  # type: ignore[attr-defined]
                logger.info("[ToolCallRuntime] Auto-repaired args for '%s'", tool_name)
        except Exception as exc:
            logger.debug("[ToolCallRuntime] Arg repair failed for '%s': %s", tool_name, exc)

        return request

    def _increment_usage(self, tool_name: str) -> None:
        self._control_runtime.increment_usage(tool_name)
        self._inventory.increment_usage(tool_name)

    def _run_before_tool_hooks(
        self,
        *,
        request: Any,
        tool_name: str,
        tool_args: dict[str, Any],
        tool_call_id: str,
        is_dynamic: bool,
    ) -> tuple[Any, dict[str, Any], ToolMessage | None]:
        hook_context = self._plugin_registry.run_before_tool_call(
            ToolCallHookContext(
                tool_name=tool_name,
                arguments=dict(tool_args),
                tool_call_id=tool_call_id,
                phase="before",
            )
        )
        normalized_args = hook_context.arguments if isinstance(hook_context.arguments, dict) else dict(tool_args)
        if normalized_args != tool_args:
            self._set_tool_args(request.tool_call, normalized_args)
            tool_args = normalized_args

        if not hook_context.cancel:
            return request, tool_args, None

        reason = hook_context.cancel_reason.strip() or f"插件已阻止工具调用: {tool_name}"
        self._control_runtime.record_control_event(
            tool_name=tool_name,
            tool_args=tool_args,
            decision=ToolControlDecision(
                allowed=False,
                risk_level=ToolRiskLevel.HIGH if is_dynamic else ToolRiskLevel.MEDIUM,
                reason=reason,
                control_tags=("plugin-hook", "plugin-blocked"),
            ),
            tool_call_id=tool_call_id,
        )
        return (
            request,
            tool_args,
            self._plugin_blocked_message(
                tool_name=tool_name,
                tool_call_id=tool_call_id,
                reason=reason,
            ),
        )

    def _run_after_tool_hooks(
        self,
        *,
        tool_name: str,
        tool_args: dict[str, Any],
        tool_call_id: str,
        result: ToolMessage | Command,
        exec_time: float,
        is_dynamic: bool,
    ) -> ToolMessage | Command:
        hook_context = self._plugin_registry.run_after_tool_call(
            ToolCallHookContext(
                tool_name=tool_name,
                arguments=dict(tool_args),
                tool_call_id=tool_call_id,
                result=result,
                duration=exec_time,
                phase="after",
            )
        )
        updated_result = hook_context.result if hook_context.result is not None else result
        if hook_context.cancel:
            reason = hook_context.cancel_reason.strip() or f"插件已阻止工具结果输出: {tool_name}"
            self._control_runtime.record_control_event(
                tool_name=tool_name,
                tool_args=tool_args,
                decision=ToolControlDecision(
                    allowed=False,
                    risk_level=ToolRiskLevel.HIGH if is_dynamic else ToolRiskLevel.MEDIUM,
                    reason=reason,
                    control_tags=("plugin-hook", "plugin-result-blocked"),
                ),
                tool_call_id=tool_call_id,
            )
            if not isinstance(updated_result, ToolMessage) or updated_result.status != "error":
                return self._plugin_blocked_message(
                    tool_name=tool_name,
                    tool_call_id=tool_call_id,
                    reason=reason,
                )
        return updated_result

    @staticmethod
    def _error_to_tool_message(
        exc: Exception,
        tool_name: str,
        tool_call_id: str,
    ) -> ToolMessage:
        error_msg = format_error(exc)
        retry_hint = ""
        if isinstance(exc, (ConnectionError, TimeoutError, OSError, ToolTimeoutError)):
            retry_hint = " 这是一个临时错误，可以重试。"
        elif isinstance(exc, ToolRateLimitError):
            retry_hint = f" 请等待 {exc.retry_after_seconds}s 后重试。" if exc.retry_after_seconds else " 请稍后重试。"
        content = json.dumps(
            {"error": error_msg + retry_hint, "tool": tool_name, "recoverable": bool(retry_hint)},
            ensure_ascii=False,
        )
        return ToolMessage(content=content, tool_call_id=tool_call_id, status="error")

    def _emit_dynamic_tool_semantic_failure(self, tool_name: str, payload: dict[str, Any]) -> None:
        """Feed Admin / telemetry when a dynamic tool returns success=false in-band."""
        from core.systems.runtime.event_bus import Event, EventType, event_bus

        tb = str(payload.get("traceback", "") or payload.get("error", "") or "")
        tags = ["dynamic-tool-semantic-failure"]
        if "UnicodeDecodeError" in tb or "codec can't decode" in tb.lower():
            tags.append("subprocess-encoding")
        if "timeout" in tb.lower() or "timed out" in tb.lower():
            tags.append("timeout")
        event_bus.emit(
            Event(
                type=EventType.TOOL_RESULT,
                source=f"Tool:{tool_name}",
                session_id=str(self._event_context().get("thread_id", "")).strip() or None,
                payload={
                    "success": False,
                    "semantic_failure": True,
                    "tags": tags,
                    "error": str(payload.get("error", ""))[:800],
                    "suggestion": str(payload.get("suggestion", ""))[:400],
                },
            )
        )

    def _record_execution_error(
        self,
        *,
        tool_name: str,
        tool_args: dict[str, Any],
        tool_call_id: str,
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

        self._emit_tool_event(
            event_type="tool_result",
            tool_name=tool_name,
            tool_args=tool_args,
            tool_call_id=tool_call_id,
            status="error",
            is_dynamic=is_dynamic,
            preview=str(error),
            error=str(error),
        )

    @staticmethod
    def _plugin_blocked_message(
        *,
        tool_name: str,
        tool_call_id: str,
        reason: str,
    ) -> ToolMessage:
        return ToolMessage(
            content=json.dumps(
                {
                    "error": reason,
                    "tool": tool_name,
                    "blocked_by_plugin": True,
                },
                ensure_ascii=False,
            ),
            tool_call_id=tool_call_id,
            status="error",
        )

    @staticmethod
    def _set_tool_args(tool_call: Any, tool_args: dict[str, Any]) -> None:
        if isinstance(tool_call, dict):
            tool_call["args"] = dict(tool_args)
            return
        if hasattr(tool_call, "args"):
            tool_call.args = dict(tool_args)  # type: ignore[attr-defined]

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
