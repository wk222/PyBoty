"""Factory helpers for PyBot root modes."""

from __future__ import annotations

from typing import Any, Callable, TYPE_CHECKING

if TYPE_CHECKING:
    from langchain_core.language_models import BaseChatModel
    from core.systems.runtime.project_paths import ProjectPaths
    from core.systems.runtime.pybot_bootstrap import PyBotRuntime, ToolAssembly


def build_mode_subclasses(pybot_cls: type[Any]) -> tuple[type[Any], type[Any], type[Any]]:
    """Create the public root-mode subclasses from the base PyBot runtime."""

    class AdminPyBot(pybot_cls):
        """Separate root runtime for long-running admin orchestration."""

        def __init__(self, *args, **kwargs):
            kwargs.setdefault("root_mode", "admin")
            kwargs.setdefault("attach_admin_runtime", True)
            super().__init__(*args, **kwargs)

    class UltimatePyBot(AdminPyBot):
        """User-facing alias for the ultimate-agent mode."""

    class AppMatrixPyBot(pybot_cls):
        """Root runtime for APP-level orchestration and central scheduling."""

        def __init__(self, *args, **kwargs):
            kwargs.setdefault("root_mode", "app_matrix")
            kwargs.setdefault("attach_admin_runtime", True)
            super().__init__(*args, **kwargs)

    return AdminPyBot, UltimatePyBot, AppMatrixPyBot


def create_mode_agent(
    agent_cls: type[Any],
    *,
    model: str,
    thread_id: str,
    paths: Any = None,
    control_config: dict[str, Any] | None = None,
    approval_queue: Any = None,
    **kwargs,
) -> Any:
    """Instantiate a concrete root-mode runtime with the standard public kwargs."""
    return agent_cls(
        model=model,
        thread_id=thread_id,
        paths=paths,
        control_config=control_config,
        approval_queue=approval_queue,
        **kwargs,
    )


def assemble_primary_tools(
    *,
    runtime: PyBotRuntime,
    paths: ProjectPaths,
    enable_agent_creation: bool,
    root_mode: str,
    llm_factory: Callable[[str | None, float | None], BaseChatModel],
    chat_callback: Callable[[str], str],
) -> ToolAssembly:
    """Resolve root-agent tools and prompt extensions by pulling from all capability layers."""
    from core.systems.runtime.pybot_bootstrap import ToolAssembly
    from core.assets.tools import (
        get_tool_creator_tools,
        get_clarification_tools,
        get_tool_chain_tools,
        get_template_prompt_section,
    )
    from core.assets.skills.skill_marketplace import get_marketplace_tools
    from core.modes.agents.agent_creator import get_agent_creator_tools
    from core.modes.apps import app_runtime, app_orchestration
    from core.assets.workflows import workflow_runtime
    from core.systems.eval.eval_framework import get_eval_tools
    from core.systems.bus.capability_bus import get_capability_bus_tools
    from core.systems.bus.capability_registry import get_capability_registry_tools
    from core.systems.memory.memory_tools import get_memory_tools
    from core.assets.tools.permission_tools import get_permission_tools
    from core.systems.execution import get_execution_loop_tools
    from core.assets.tools.bash_tool import BashTool
    from core.assets.tools.web_fetch_tool import WebFetchTool
    from core.systems.runtime.prompts import (
        build_static_system_prompt,
    )

    creator_tools = get_tool_creator_tools(
        storage=runtime.storage,
        agent_storage=runtime.agent_storage,
    )
    tool_groups: list[tuple[str, int]] = []

    if enable_agent_creation:
        agent_tools = get_agent_creator_tools(
            agent_storage=runtime.agent_storage,
            tool_storage=runtime.storage,
            llm_factory=llm_factory,
            control_policy=runtime.control_policy,
            approval_queue=runtime.approval_queue,
            project_paths=paths,
            subagent_registry=runtime.subagent_registry,
            runtime_context={"current_agent_name": "root", "depth": 0},
        )
        creator_tools.extend(agent_tools)
        tool_groups.append(("智能体工具", len(agent_tools)))

    app_tools = app_runtime.creator_tools_factory(llm=llm_factory(None, None))
    creator_tools.extend(app_tools)
    tool_groups.append(("应用工具", len(app_tools)))

    app_marketplace_tools = app_orchestration.marketplace_tools_factory()
    creator_tools.extend(app_marketplace_tools)
    tool_groups.append(("应用集市工具", len(app_marketplace_tools)))

    clarification_tools = get_clarification_tools()
    creator_tools.extend(clarification_tools)
    tool_groups.append(("澄清工具", len(clarification_tools)))

    verifier_tools = app_runtime.verifier_tools_factory()
    creator_tools.extend(verifier_tools)
    tool_groups.append(("验证工具", len(verifier_tools)))

    marketplace_tools = get_marketplace_tools(runtime.skill_marketplace)
    creator_tools.extend(marketplace_tools)
    tool_groups.append(("市场工具", len(marketplace_tools)))

    pyflow_tools = workflow_runtime.tools_factory(runtime.pyflow_engine)
    creator_tools.extend(pyflow_tools)
    tool_groups.append(("工作流工具", len(pyflow_tools)))

    exec_tools = get_execution_loop_tools(str(paths.workspace_dir))
    creator_tools.extend(exec_tools)
    tool_groups.append(("执行工具", len(exec_tools)))

    chain_tools = get_tool_chain_tools(runtime.tool_chain)
    creator_tools.extend(chain_tools)
    tool_groups.append(("链式工具", len(chain_tools)))

    eval_tools = get_eval_tools(runtime.eval_framework)
    creator_tools.extend(eval_tools)
    tool_groups.append(("评估工具", len(eval_tools)))

    bus_tools = get_capability_bus_tools(runtime.capability_bus)
    creator_tools.extend(bus_tools)
    tool_groups.append(("能力总线", len(bus_tools)))

    registry_tools = get_capability_registry_tools(runtime.capability_registry)
    creator_tools.extend(registry_tools)
    tool_groups.append(("能力注册中心", len(registry_tools)))

    mcp_tools = runtime.mcp_hub.get_tools()
    if mcp_tools:
        creator_tools.extend(mcp_tools)
        tool_groups.append(("MCP 工具", len(mcp_tools)))

    if runtime.knowledge_tools:
        creator_tools.extend(runtime.knowledge_tools)
        tool_groups.append(("知识检索", len(runtime.knowledge_tools)))

    mem_tools = get_memory_tools(runtime.memory)
    creator_tools.extend(mem_tools)
    tool_groups.append(("记忆工具", len(mem_tools)))

    permission_tools = get_permission_tools(runtime.middleware)
    creator_tools.extend(permission_tools)
    tool_groups.append(("权限治理工具", len(permission_tools)))

    if runtime.orchestration_registry is not None:
        orch_tools = app_orchestration.orchestration_tools_factory(runtime.orchestration_registry)
        creator_tools.extend(orch_tools)
        tool_groups.append(("编排工具", len(orch_tools)))

    skill_tools = runtime.skill_registry.get_active_tools()
    if skill_tools:
        creator_tools.extend(skill_tools)
        tool_groups.append(("技能工具", len(skill_tools)))

    fs_tools = get_execution_loop_tools(str(paths.workspace_dir))
    # Note: filter or add specific ones if needed, currently we add loop tools above
    # tool_groups.append(("文件系统工具", len(fs_tools)))

    bash_tool = BashTool(allowed_root=str(paths.workspace_dir))
    creator_tools.append(bash_tool)
    tool_groups.append(("Shell 工具", 1))

    web_fetch_tool = WebFetchTool()
    creator_tools.append(web_fetch_tool)
    tool_groups.append(("网页抓取工具", 1))

    runtime.capability_registry.refresh_local_index(tools=creator_tools)

    runtime.channel_manager.set_agent_callback(chat_callback)

    from core.assets.skills.markdown_loader import MarkdownSkillLoader
    markdown_loader = MarkdownSkillLoader(skills_dir=str(paths.skills_dir))
    skill_summary = markdown_loader.get_all_skills_summary()

    system_prompt = build_static_system_prompt(
        template_section=get_template_prompt_section(),
        root_mode=root_mode,
    )
    if skill_summary:
        system_prompt += f"\n\n## 预装技能摘要\n{skill_summary}"

    from core.assets.tools import get_dynamic_tools
    dynamic_tools = get_dynamic_tools(runtime.storage)
    all_tools = creator_tools + dynamic_tools

    return ToolAssembly(
        creator_tools=creator_tools,
        dynamic_tools=dynamic_tools,
        all_tools=all_tools,
        tool_groups=tool_groups,
        system_prompt=system_prompt,
    )


def build_subagent_langchain_middleware(
    *,
    definition: Any,
    sandbox: Any,  # SubagentSandbox
    capability_profile: Any,  # AgentCapabilityProfile
    middleware_profile: Any,  # AgentMiddlewareProfile
    effective_policy: Any,  # AgentControlPolicy
    tool_middleware: Any,  # DynamicToolMiddleware
    vector_store: Any | None = None,
    distill_fn: Any | None = None,
) -> list[Any]:
    """Assemble the subagent LangChain middleware stack."""
    from core.systems.middleware.loop_guard_middleware import LoopGuardMiddleware
    from core.systems.middleware.insight_vault_middleware import InsightVaultMiddleware
    from core.systems.middleware.reasoning_frame_middleware import ReasoningFrameMiddleware
    from core.systems.middleware.agent_prompt_middleware import PromptSectionMiddleware
    from core.assets.tools import ToolArgRepairMiddleware
    from core.systems.runtime.patch_tool_calls import PatchToolCallsMiddleware

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
    sandbox: Any,
    capability_profile: Any,
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
    effective_policy: Any,
    capability_profile: Any,
    middleware_profile: Any,
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
    capability_profile: Any,
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
    sandbox: Any,
    capability_profile: Any,
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
