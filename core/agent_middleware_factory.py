"""Factories for assembling LangChain middleware stacks."""

from __future__ import annotations

from typing import Any

from .agent_capability_profile import AgentCapabilityProfile
from .agent_control import AgentControlPolicy
from .agent_middleware_profile import AgentMiddlewareProfile
from .agent_prompt_middleware import PromptSectionMiddleware
from .lc_bus_middleware import LCBusMiddleware
from .lc_memory_middleware import LCMemoryMiddleware
from .patch_tool_calls import PatchToolCallsMiddleware
from .prompts import build_runtime_prompt_sections
from .subagent_sandbox import SubagentSandbox
from .summarization_middleware import SummarizationConfig, SummarizationMiddleware
from .todo_middleware import TodoListMiddleware
from .tool_eviction_middleware import LCToolEvictionMiddleware
from .tool_middleware import DynamicToolMiddleware


def build_root_langchain_middleware(
    *,
    runtime: Any,
    summarize_fn: Any | None = None,
    summarization_config: SummarizationConfig | None = None,
    eviction_dir: str | None = None,
) -> list[Any]:
    """Assemble the root-agent LangChain middleware stack.

    Ordering follows DeepAgents convention:
      TodoList → PromptContext → Memory → Summarization → BusRecorder →
      ToolEviction → DynamicToolMiddleware → AnthropicCaching → PatchToolCalls
    """
    stack: list[Any] = [
        TodoListMiddleware(),
        PromptSectionMiddleware(
            name="RootPromptContextMiddleware",
            prompt_builder=lambda: build_runtime_prompt_sections(
                workspace_context=runtime.workspace.build_system_context(),
                memory_context=runtime.memory.get_context_prompt(),
                skill_extensions=runtime.skill_registry.get_active_prompt_extensions(progressive=True),
            ),
        ),
        LCMemoryMiddleware(runtime.memory),
        SummarizationMiddleware(
            summarize_fn=summarize_fn,
            config=summarization_config,
        ),
        LCBusMiddleware(runtime.capability_bus),
    ]
    if eviction_dir:
        stack.append(LCToolEvictionMiddleware(eviction_dir=eviction_dir))
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
) -> list[Any]:
    """Assemble the subagent LangChain middleware stack."""
    stack: list[Any] = []
    for section in middleware_profile.stack_names():
        if section == "prompt_context":
            stack.append(
                PromptSectionMiddleware(
                    name=f"SubagentPromptContextMiddleware:{definition.name}",
                    prompt_builder=lambda: build_subagent_runtime_prompt_sections(
                        agent_name=definition.name,
                        role=definition.role,
                        sandbox=sandbox,
                        capability_profile=capability_profile,
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
    return "\n".join(
        [
            "## 子智能体运行上下文",
            f"- 名称: {agent_name}",
            f"- 角色: {role}",
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
