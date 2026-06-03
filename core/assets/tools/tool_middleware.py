"""LangChain middleware for dynamic tools, approvals, and permission control."""

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


from core.systems.governance.agent_control import AgentControlPolicy
from core.systems.governance.approval_queue import ApprovalQueue
from core.systems.governance.permission_policy import (
    PermissionControlPlane,
    PermissionMode,
    RuleVerdict,
)
from core.systems.runtime.hooks_runtime import HookPhase
from core.systems.runtime.projected_runtime_view import build_projected_runtime_view, extract_projected_runtime_view
from core.systems.runtime.trusted_settings import TrustedSettingsBundle
from core.systems.integration.plugin_manifest import PluginRegistry

from .tool_middleware_factory import build_tool_middleware_components
from .tool_storage import ToolStorage


class DynamicToolMiddleware(AgentMiddleware if LANGCHAIN_1_AVAILABLE else object):
    """Inject dynamic tools and enforce governance at tool-call time."""

    def __init__(
        self,
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
        ask_user_fn: Callable[[str, str], bool] | None = None,
        permission_policy: PermissionControlPlane | None = None,
        trusted_settings: TrustedSettingsBundle | None = None,
        hooks_runtime: Any | None = None,
        runtime_view_provider: Callable[[], dict[str, Any] | None] | None = None,
    ):
        components = build_tool_middleware_components(
            tool_storage=tool_storage,
            control_policy=control_policy,
            approval_queue=approval_queue,
            approval_scope=approval_scope,
            stuck_loop_threshold=stuck_loop_threshold,
            stuck_loop_kill_threshold=stuck_loop_kill_threshold,
            allowed_path_roots=allowed_path_roots,
            plugin_registry=plugin_registry,
            event_context_resolver=event_context_resolver,
        )
        self.control_policy = components.control_policy
        self.approval_queue = components.approval_queue
        self.approval_scope = approval_scope
        self._inventory = components.inventory
        self._control_runtime = components.control_runtime
        self._tool_call_runtime = components.tool_call_runtime
        self._model_runtime = components.model_runtime
        self._ask_user_fn = ask_user_fn
        self._trusted_settings = trusted_settings
        if permission_policy is not None:
            self._permission_policy = permission_policy
        else:
            self._permission_policy = PermissionControlPlane.from_trusted_settings(trusted_settings)
        self._hooks_runtime = hooks_runtime
        self._runtime_view_provider = runtime_view_provider

    @property
    def name(self) -> str:
        return "DynamicToolMiddleware"

    @property
    def tools(self) -> list[BaseTool]:
        return self._inventory.list_dynamic_tools()

    @property
    def last_created_tool(self) -> str | None:
        return self._inventory.last_created_tool

    @property
    def permission_policy(self) -> PermissionControlPlane:
        return self._permission_policy

    @property
    def permission_control(self) -> PermissionControlPlane:
        return self._permission_policy

    def set_ask_user_fn(self, ask_user_fn: Callable[[str, str], bool] | None) -> None:
        self._ask_user_fn = ask_user_fn

    def set_runtime_view_provider(
        self,
        runtime_view_provider: Callable[[], dict[str, Any] | None] | None,
    ) -> None:
        self._runtime_view_provider = runtime_view_provider

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
        snapshot = self._control_runtime.build_snapshot(
            known_tools=[tool.name for tool in self.get_all_tools()],
            approval_queue=self.approval_queue.get_snapshot() if self.approval_queue is not None else {},
        )
        if isinstance(snapshot, dict):
            snapshot["permission"] = self.get_permission_snapshot()
            snapshot["settings"] = self.get_settings_projection()
        return snapshot

    def get_permission_snapshot(self) -> dict[str, Any]:
        return self._permission_policy.get_snapshot()

    def get_trusted_settings(self) -> TrustedSettingsBundle | None:
        return self._trusted_settings

    def get_settings_projection(self) -> dict[str, Any]:
        if self._trusted_settings is None or not hasattr(self._trusted_settings, "build_projection"):
            return {}
        try:
            return self._trusted_settings.build_projection()
        except Exception:
            return {}

    def _sync_trusted_settings_from_permission(self) -> None:
        if self._trusted_settings is None or not hasattr(self._trusted_settings, "with_permission_source"):
            return
        try:
            session_permission = self._permission_policy.export_source_settings(source="session").get("permission", {})
            self._trusted_settings = self._trusted_settings.with_permission_source(
                source="session",
                mode=str(session_permission.get("mode", "")).strip(),
                rules=session_permission.get("rules", {}) if isinstance(session_permission.get("rules", {}), dict) else {},
                path="session://runtime",
            )
        except Exception:
            pass

    def set_permission_mode(self, mode: PermissionMode | str) -> dict[str, Any]:
        self._permission_policy.set_mode(mode)
        self._sync_trusted_settings_from_permission()
        return self.get_permission_snapshot()

    def add_permission_rule(
        self,
        tool_name: str,
        verdict: RuleVerdict | str,
        *,
        reason: str = "",
        source: str = "session",
    ) -> dict[str, Any]:
        self._permission_policy.add_rule(tool_name=tool_name, verdict=verdict, reason=reason, source=source)
        self._sync_trusted_settings_from_permission()
        return self.get_permission_snapshot()

    def remove_permission_rule(self, tool_name: str) -> dict[str, Any]:
        self._permission_policy.remove_rule(tool_name)
        self._sync_trusted_settings_from_permission()
        return self.get_permission_snapshot()

    def clear_permission_rules(self) -> dict[str, Any]:
        self._permission_policy.clear_rules()
        self._sync_trusted_settings_from_permission()
        return self.get_permission_snapshot()

    def hydrate_permission_policy(self, trusted_settings: TrustedSettingsBundle) -> dict[str, Any]:
        self._trusted_settings = trusted_settings
        self._permission_policy.load_trusted_settings(trusted_settings)
        return self.get_permission_snapshot()

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

    def _deny_message(self, *, tool_name: str, tool_call_id: str, mode: str) -> ToolMessage:
        return ToolMessage(
            content=(
                f"Permission mode [{mode}] denied tool `{tool_name}`.\n"
                "Switch back to `default` or `bypass` if this mutation is intentionally allowed."
            ),
            tool_call_id=tool_call_id,
            name=tool_name,
            status="error",
        )

    def _user_rejected_message(self, *, tool_name: str, tool_call_id: str) -> ToolMessage:
        return ToolMessage(
            content=f"User rejected approval for `{tool_name}`. The tool call was canceled.",
            tool_call_id=tool_call_id,
            name=tool_name,
            status="error",
        )

    def _projected_runtime_view_for_hooks(self) -> dict[str, Any]:
        if self._runtime_view_provider is not None:
            try:
                payload = self._runtime_view_provider()
                runtime_view = extract_projected_runtime_view(payload)
                if runtime_view is not None:
                    return runtime_view.to_payload()
            except Exception:
                pass
        return build_projected_runtime_view(
            thread_id="default",
            root_mode="assistant",
            permission=self._permission_policy.build_projection(),
            settings=self.get_settings_projection() or {"permission_mode": self._permission_policy.mode.value},
        ).to_payload()

    def _check_governance_approval(
        self,
        tool_name: str,
        tool_args: Any,
        *,
        tool_call_id: str = "",
    ) -> ToolMessage | None:
        tool_map = {tool.name: tool for tool in self._inventory.get_all_tools()}
        tool = tool_map.get(tool_name)
        tool_risk = getattr(tool, "risk_level", "low") if tool is not None else "low"
        verdict = self._permission_policy.evaluate(tool_name, tool_risk)
        reason_fragments: list[str] = ["pre_tool_call_check"]
        if self._hooks_runtime is not None and hasattr(self._hooks_runtime, "run_phase"):
            try:
                hook_result = self._hooks_runtime.run_phase(
                    HookPhase.PERMISSION_DECISION,
                    {
                        "tool_name": tool_name,
                        "tool_args": tool_args,
                        "tool_risk": tool_risk,
                        "projected_runtime_view": self._projected_runtime_view_for_hooks(),
                    },
                )
                hook_verdict = str(hook_result.get("verdict", "")).strip().lower()
                if hook_verdict:
                    current_rank = {"allow": 0, "ask": 1, "deny": 2}.get(verdict.value, 0)
                    incoming_rank = {"allow": 0, "ask": 1, "deny": 2}.get(hook_verdict, 0)
                    if incoming_rank > current_rank:
                        verdict = RuleVerdict(hook_verdict)
                for fragment in hook_result.get("reason_fragments", []):
                    text = str(fragment).strip()
                    if text and text not in reason_fragments:
                        reason_fragments.append(text)
            except Exception:
                pass
        self._permission_policy.record_runtime_decision(
            tool_name=tool_name,
            verdict=verdict,
            tool_risk=tool_risk,
            reason="; ".join(reason_fragments),
        )

        if verdict == RuleVerdict.DENY:
            return self._deny_message(tool_name=tool_name, tool_call_id=tool_call_id, mode=self._permission_policy.mode.value)
        if verdict == RuleVerdict.ALLOW:
            return None

        args_str = str(tool_args)
        if self._ask_user_fn is not None:
            approved = self._ask_user_fn(tool_name, args_str)
            if approved:
                self._permission_policy.record_runtime_decision(
                    tool_name=tool_name,
                    verdict=RuleVerdict.ALLOW,
                    tool_risk=tool_risk,
                    reason="user_approved",
                )
                return None
            self._permission_policy.record_runtime_decision(
                tool_name=tool_name,
                verdict=RuleVerdict.DENY,
                tool_risk=tool_risk,
                reason="user_rejected",
            )
            return self._user_rejected_message(tool_name=tool_name, tool_call_id=tool_call_id)

        if self.approval_queue is not None:
            from core.systems.governance.approval_callback import ApprovalRequiredException

            try:
                self.approval_queue.create_request(
                    kind="TOOL_APPROVAL",
                    scope=self.approval_scope,
                    summary=f"High-risk tool '{tool_name}' requested execution",
                    prompt=f"Tool: {tool_name}\nArgs: {args_str}",
                    metadata={"tool_name": tool_name, "tool_input": args_str},
                )
            except Exception:
                pass
            raise ApprovalRequiredException(tool_name=tool_name, tool_input=args_str)

        return None

    def wrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], ToolMessage | Command],
    ) -> ToolMessage | Command:
        if LANGCHAIN_1_AVAILABLE:
            if isinstance(request.tool_call, dict):
                tool_name = request.tool_call.get("name", "")
                tool_args = request.tool_call.get("args", {})
                tool_id = request.tool_call.get("id", "")
            else:
                tool_name = getattr(request.tool_call, "name", "")
                tool_args = getattr(request.tool_call, "args", {})
                tool_id = getattr(request.tool_call, "id", "")
            block = self._check_governance_approval(tool_name, tool_args, tool_call_id=tool_id)
            if block is not None:
                return block
        return self._tool_call_runtime.run_tool_call(request, handler)

    async def awrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], Awaitable[ToolMessage | Command]],
    ) -> ToolMessage | Command:
        if LANGCHAIN_1_AVAILABLE:
            if isinstance(request.tool_call, dict):
                tool_name = request.tool_call.get("name", "")
                tool_args = request.tool_call.get("args", {})
                tool_id = request.tool_call.get("id", "")
            else:
                tool_name = getattr(request.tool_call, "name", "")
                tool_args = getattr(request.tool_call, "args", {})
                tool_id = getattr(request.tool_call, "id", "")
            block = self._check_governance_approval(tool_name, tool_args, tool_call_id=tool_id)
            if block is not None:
                return block
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
