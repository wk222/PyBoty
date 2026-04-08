"""Factories for assembling LangChain middleware stacks."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from core.assets.agents.agent_capability_profile import AgentCapabilityProfile
from core.assets.agents.agent_middleware_profile import AgentMiddlewareProfile
from core.systems.bus.lc_bus_middleware import LCBusMiddleware
from core.systems.runtime.patch_tool_calls import PatchToolCallsMiddleware
from core.systems.runtime.instruction_assembly import InstructionAssembly
from core.systems.governance.subagent_sandbox import SubagentSandbox
from core.systems.governance.agent_control import AgentControlPolicy
from core.assets.tools.tool_arg_repair_middleware import ToolArgRepairMiddleware
from core.assets.tools.tool_eviction_middleware import LCToolEvictionMiddleware
from core.assets.tools.tool_middleware import DynamicToolMiddleware

from .agent_prompt_middleware import PromptSectionMiddleware
from .insight_vault_middleware import InsightVaultConfig, InsightVaultMiddleware
from .lc_memory_middleware import LCMemoryMiddleware
from .loop_guard_middleware import LoopGuardConfig, LoopGuardMiddleware
from .reasoning_frame_middleware import ReasoningFrameConfig, ReasoningFrameMiddleware
from .summarization_middleware import SummarizationConfig, SummarizationMiddleware
from .todo_middleware import TodoListMiddleware


def build_root_langchain_middleware(
    *,
    runtime: Any,
    summarize_fn: Any | None = None,
    summarization_config: SummarizationConfig | None = None,
    session_compaction_callback: Any | None = None,
    session_memory_extractor: Any | None = None,
    eviction_dir: str | None = None,
    loop_guard_config: LoopGuardConfig | None = None,
    insight_vault_config: InsightVaultConfig | None = None,
    reasoning_frame_config: ReasoningFrameConfig | None = None,
    vector_store: Any | None = None,
    runtime_view_provider: Callable[[], dict | None] | None = None,
) -> list[Any]:
    """Assemble the root-agent LangChain middleware stack.

    Ordering:
      LoopGuard → TodoList → PromptContext → InsightVault → ReasoningFrame →
      Memory → Summarization → BusRecorder → ToolEviction →
      DynamicToolMiddleware → AnthropicCaching → PatchToolCalls
    """
    todo_middleware = TodoListMiddleware(task_runtime=getattr(runtime, "task_runtime", None))

    def _resolve_runtime_tool_projection() -> dict | None:
        tool_middleware = getattr(runtime, "middleware", None)
        if tool_middleware is None or not hasattr(tool_middleware, "get_control_snapshot"):
            return None
        try:
            snapshot = tool_middleware.get_control_snapshot()
        except Exception:
            return None
        observability = snapshot.get("observability", {}) if isinstance(snapshot, dict) else {}
        recent_events = observability.get("recent_events", []) if isinstance(observability, dict) else []
        runs: list[dict[str, Any]] = []
        for event in recent_events[-6:]:
            if not isinstance(event, dict):
                continue
            tool_name = str(event.get("tool_name", "")).strip()
            if not tool_name:
                continue
            status = "completed"
            if event.get("requires_approval"):
                status = "approval_required"
            elif not event.get("allowed", True):
                status = "blocked"
            runs.append(
                {
                    "title": tool_name,
                    "status": status,
                    "source": "tool_control",
                    "run_id": str(event.get("tool_call_id", "")).strip(),
                    "preview": str(event.get("args_preview", "")).strip(),
                    "timestamp": event.get("timestamp"),
                }
            )
        task_runtime = getattr(runtime, "task_runtime", None)
        if task_runtime is not None:
            try:
                task_runtime.ingest_tool_runs(runs, source="tool_control")
                permission_snapshot = snapshot.get("permission", {}) if isinstance(snapshot, dict) else {}
                if isinstance(permission_snapshot, dict):
                    task_runtime.ingest_permission_events(
                        list(permission_snapshot.get("recent_events", [])),
                        source="permission_projection",
                    )
            except Exception:
                pass
        if not runs:
            return None
        return {"recent_tool_runs": runs}

    def _resolve_task_runtime_projection() -> dict | None:
        task_runtime = getattr(runtime, "task_runtime", None)
        if task_runtime is None or not hasattr(task_runtime, "build_projection"):
            return None
        try:
            return task_runtime.build_projection()
        except Exception:
            return None

    def _resolve_resume_artifacts() -> dict | None:
        base_artifacts = runtime_view_provider() if runtime_view_provider is not None else None
        tool_projection = _resolve_runtime_tool_projection()
        todo_projection = todo_middleware.export_projection()
        task_runtime_projection = _resolve_task_runtime_projection()
        if todo_projection is None and tool_projection is None and task_runtime_projection is None:
            return base_artifacts
        from core.systems.runtime.session.session_runtime_view import (
            compile_runtime_resume_view,
            runtime_view_from_resume_dict,
        )
        from core.systems.runtime.projected_runtime_view import (
            build_projected_runtime_view,
            build_runtime_task_section,
            merge_projected_runtime_views,
        )

        system_context = dict(base_artifacts.get("system_context", {})) if isinstance(base_artifacts, dict) else {}
        overlay_view = build_projected_runtime_view(
            thread_id=str(system_context.get("thread_id", "")).strip() or "default",
            root_mode=str(system_context.get("primary_mode", "")).strip() or "assistant",
            system_context=system_context,
            tasks=build_runtime_task_section(
                task_runtime=task_runtime_projection or {},
                task_projection=todo_projection or {},
                recent_tool_runs=(
                    list((tool_projection or {}).get("recent_tool_runs", []))
                    if isinstance(tool_projection, dict)
                    else []
                ),
            ),
        )
        merged_view = merge_projected_runtime_views(runtime_view_from_resume_dict(base_artifacts), overlay_view) or overlay_view
        return compile_runtime_resume_view(merged_view)

    def _resolve_resume_runtime_view() -> dict | None:
        artifacts = _resolve_resume_artifacts()
        if not isinstance(artifacts, dict):
            return None
        runtime_view = artifacts.get("projected_runtime_view")
        return dict(runtime_view) if isinstance(runtime_view, dict) and runtime_view else None

    stack: list[Any] = [
        LoopGuardMiddleware(config=loop_guard_config),
        todo_middleware,
        PromptSectionMiddleware(
            name="RootPromptContextMiddleware",
            prompt_builder=lambda: InstructionAssembly.build_runtime_sections(
                workspace_context=runtime.workspace.build_system_context(),
                memory_context=runtime.memory.get_context_prompt(),
                skill_extensions=runtime.skill_registry.get_active_prompt_extensions(progressive=True),
                projected_runtime_view=_resolve_resume_runtime_view(),
                hooks_runtime=getattr(runtime, "hooks_runtime", None),
            ),
        ),
        InsightVaultMiddleware(
            vector_store=vector_store,
            config=insight_vault_config,
        ),
        ReasoningFrameMiddleware(config=reasoning_frame_config),
        LCMemoryMiddleware(runtime.memory),
        SummarizationMiddleware(
            summarize_fn=summarize_fn,
            config=summarization_config,
            compaction_callback=session_compaction_callback,
            session_memory_extractor=session_memory_extractor,
            runtime_view_provider=_resolve_resume_runtime_view,
            hooks_runtime=getattr(runtime, "hooks_runtime", None),
        ),
        LCBusMiddleware(runtime.capability_bus),
    ]
    if eviction_dir:
        stack.append(LCToolEvictionMiddleware(eviction_dir=eviction_dir))

    arg_repair = ToolArgRepairMiddleware()
    stack.append(arg_repair)

    stack.append(runtime.middleware)
    try:
        from langchain_anthropic.chat_models import AnthropicPromptCachingMiddleware

        stack.append(AnthropicPromptCachingMiddleware(unsupported_model_behavior="ignore"))
    except ImportError:
        pass
    stack.append(PatchToolCallsMiddleware())
    return stack


def build_subagent_langchain_middleware(
    *,
    definition: Any,
    sandbox: SubagentSandbox,
    capability_profile: AgentCapabilityProfile,
    middleware_profile: AgentMiddlewareProfile,
    effective_policy: AgentControlPolicy,
    tool_middleware: DynamicToolMiddleware,
    vector_store: Any | None = None,
    distill_fn: Any | None = None,
) -> list[Any]:
    """Assemble the subagent LangChain middleware stack.

    Enhancement sections (loop_guard, insight_vault, reasoning_frame) are
    created only when present in the middleware profile, giving the parent
    agent full control over which enhancements each child receives.
    """
    stack: list[Any] = []
    for section in middleware_profile.stack_names():
        if section == "loop_guard":
            stack.append(LoopGuardMiddleware())
            continue
        if section == "insight_vault":
            stack.append(InsightVaultMiddleware(
                vector_store=vector_store,
                distill_fn=distill_fn,
            ))
            continue
        if section == "reasoning_frame":
            stack.append(ReasoningFrameMiddleware())
            continue
        if section == "prompt_context":
            stack.append(
                PromptSectionMiddleware(
                    name=f"SubagentPromptContextMiddleware:{definition.name}",
                    prompt_builder=lambda: build_subagent_runtime_prompt_sections(
                        agent_name=definition.name,
                        role=definition.role,
                        sandbox=sandbox,
                        capability_profile=capability_profile,
                        goal=getattr(definition, "goal", ""),
                        backstory=getattr(definition, "backstory", ""),
                    ),
                )
            )
            continue
        if section == "policy_context":
            stack.append(
                PromptSectionMiddleware(
                    name=f"SubagentPolicyContextMiddleware:{definition.name}",
                    prompt_builder=lambda: build_subagent_policy_prompt_sections(
                        effective_policy=effective_policy,
                        capability_profile=capability_profile,
                        middleware_profile=middleware_profile,
                    ),
                )
            )
            continue
        if section == "delegation_context":
            stack.append(
                PromptSectionMiddleware(
                    name=f"SubagentDelegationContextMiddleware:{definition.name}",
                    prompt_builder=lambda: build_subagent_delegation_prompt_sections(
                        capability_profile=capability_profile,
                    ),
                )
            )
            continue
        if section == "execution_context":
            stack.append(
                PromptSectionMiddleware(
                    name=f"SubagentExecutionContextMiddleware:{definition.name}",
                    prompt_builder=lambda: build_subagent_execution_prompt_sections(
                        sandbox=sandbox,
                        capability_profile=capability_profile,
                    ),
                )
            )
            continue
        if section == "tool_arg_repair":
            stack.append(ToolArgRepairMiddleware())
            continue
        if section == "tool_control":
            stack.append(tool_middleware)
    try:
        from langchain_anthropic.chat_models import AnthropicPromptCachingMiddleware

        stack.append(AnthropicPromptCachingMiddleware(unsupported_model_behavior="ignore"))
    except ImportError:
        pass
    stack.append(PatchToolCallsMiddleware())
    return stack or [tool_middleware, PatchToolCallsMiddleware()]


def build_subagent_runtime_prompt_sections(
    *,
    agent_name: str,
    role: str,
    sandbox: SubagentSandbox,
    capability_profile: AgentCapabilityProfile,
    goal: str = "",
    backstory: str = "",
) -> str:
    """Describe subagent execution boundaries as dynamic prompt context."""
    capability_lines = [
        f"- 本地动态工具: {'允许' if capability_profile.allow_local_dynamic_tools else '禁止'}",
        f"- 本地工具创建: {'允许' if capability_profile.allow_local_tool_creation else '禁止'}",
        f"- 本地工具删除: {'允许' if capability_profile.allow_local_tool_removal else '禁止'}",
        f"- 智能体创建: {'允许' if capability_profile.allow_agent_creation else '禁止'}",
        f"- 智能体委派: {'允许' if capability_profile.allow_agent_delegation else '禁止'}",
        f"- 工作流管理: {'允许' if capability_profile.allow_workflow_management else '禁止'}",
        f"- 技能安装: {'允许' if capability_profile.allow_skill_installation else '禁止'}",
        f"- 应用修改: {'允许' if capability_profile.allow_app_mutation else '禁止'}",
        f"- 代码执行: {'允许' if sandbox.allows_code_execution else '禁止'}",
    ]
    sandbox_lines = [
        f"- 模式: {sandbox.mode}",
        f"- 可见性: {sandbox.visibility}",
        f"- 工作目录: {sandbox.workspace_dir}",
        f"- 可写入: {'是' if sandbox.allows_writes else '否'}",
    ]
    identity_lines = [
        "## 子智能体运行上下文",
        f"- 名称: {agent_name}",
        f"- 角色: {role}",
    ]
    if goal:
        identity_lines.append(f"- 目标: {goal}")
    if backstory:
        identity_lines.append(f"- 背景: {backstory}")

    return "\n".join(
        [
            *identity_lines,
            "",
            "### Sandbox",
            *sandbox_lines,
            "",
            "### 能力边界",
            *capability_lines,
        ]
    )


def build_subagent_policy_prompt_sections(
    *,
    effective_policy: AgentControlPolicy,
    capability_profile: AgentCapabilityProfile,
    middleware_profile: AgentMiddlewareProfile,
) -> str:
    """Describe the enforced governance profile seen by a subagent."""
    blocked_preview = ", ".join(sorted(effective_policy.blocked_tools)[:8]) or "无"
    approval_preview = ", ".join(sorted(effective_policy.approval_required_tools)[:8]) or "无"
    return "\n".join(
        [
            "## 子智能体治理策略",
            f"- control mode: {effective_policy.mode}",
            f"- sandbox mode: {capability_profile.sandbox_mode}",
            f"- middleware stack: {', '.join(middleware_profile.stack_names())}",
            f"- blocked tools: {blocked_preview}",
            f"- approval required tools: {approval_preview}",
        ]
    )


def build_subagent_delegation_prompt_sections(
    *,
    capability_profile: AgentCapabilityProfile,
) -> str:
    """Describe delegation-specific operating rules for coordinator-style subagents."""
    if capability_profile.allow_agent_delegation:
        return "\n".join(
            [
                "## 委派约束",
                "- 只有在任务可独立拆分、且需要专门角色时才委派子智能体。",
                "- 委派前先明确目标、输入、成功标准和回传格式。",
                "- 委派结果返回后，继续整合和推进主任务，而不是把控制权完全丢给子智能体。",
            ]
        )
    return "\n".join(
        [
            "## 委派约束",
            "- 当前子智能体没有继续向下委派的权限。",
            "- 如需更多能力，请先完成本职任务并把缺口反馈给上层调度智能体。",
        ]
    )


def build_subagent_execution_prompt_sections(
    *,
    sandbox: SubagentSandbox,
    capability_profile: AgentCapabilityProfile,
) -> str:
    """Describe execution/sandbox specifics for builder-style subagents."""
    return "\n".join(
        [
            "## 执行边界",
            f"- sandbox writes: {'允许' if sandbox.allows_writes else '禁止'}",
            f"- code execution: {'允许' if capability_profile.allow_code_execution else '禁止'}",
            f"- workflow management: {'允许' if capability_profile.allow_workflow_management else '禁止'}",
            "- 优先在当前 sandbox 内完成操作，不要假设可以越界访问全局项目状态。",
        ]
    )
