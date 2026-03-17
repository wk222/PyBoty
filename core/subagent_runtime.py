"""Isolated subagent runtime helpers inspired by deepagents task workers."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from langchain_core.language_models import BaseChatModel

from .agent_capability_profile import AgentCapabilityProfile
from .agent_control import AgentControlPolicy
from .agent_middleware_factory import build_subagent_langchain_middleware
from .agent_middleware_profile import AgentMiddlewareProfile
from .agent_storage import AgentDefinition
from .approval_queue import ApprovalQueue
from .private_state import get_private_keys
from .project_paths import ProjectPaths
from .subagent_checkpointing import SubagentCheckpointBundle, build_subagent_checkpointer
from .subagent_governance import derive_subagent_control_policy, filter_tools_for_policy
from .subagent_sandbox import SubagentSandbox, build_subagent_builtin_tools, build_subagent_sandbox
from .tool_approval_runtime import (
    approval_interrupt_from_metadata,
    build_tool_approval_resume_command,
    create_tool_approval_request,
    extract_tool_approval_interrupts,
)
from .tool_middleware import DynamicToolMiddleware
from .tool_storage import ToolStorage

EXCLUDED_SUBAGENT_STATE_KEYS = get_private_keys()


def filter_subagent_state(parent_state: dict[str, Any] | None) -> dict[str, Any]:
    if not parent_state:
        return {}
    private_keys = get_private_keys()
    return {key: value for key, value in parent_state.items() if key not in private_keys}


def build_subagent_prompt(task: str, context: str = "") -> str:
    if not context:
        return task
    return f"{task}\n\n上下文：{context}"


def resolve_subagent_tools(
    *,
    agent_def: AgentDefinition,
    tool_storage: ToolStorage | None = None,
    global_tool_storage: ToolStorage | None = None,
    agent_storage: Any = None,
    llm_factory: Any = None,
    control_policy: AgentControlPolicy | None = None,
    approval_queue: ApprovalQueue | None = None,
    project_paths: ProjectPaths | None = None,
) -> list[Any]:
    from .tool_creator import get_dynamic_tools
    from .tool_runtime import build_dynamic_tool

    profile = AgentCapabilityProfile.from_value(agent_def.capability_profile)
    effective_policy = derive_subagent_control_policy(base_policy=control_policy, capability_profile=profile)
    sandbox = build_subagent_sandbox(
        agent_name=agent_def.name,
        capability_profile=profile,
        project_paths=project_paths,
    )
    tools: list[Any] = []
    seen_names: set[str] = set()
    dynamic_tool_names: set[str] = set()

    if tool_storage is not None and profile.allow_local_dynamic_tools:
        for tool in get_dynamic_tools(tool_storage):
            if tool.name in seen_names:
                continue
            tools.append(tool)
            seen_names.add(tool.name)
            dynamic_tool_names.add(tool.name)

    if global_tool_storage is not None and agent_def.tools:
        for tool_name in agent_def.tools:
            if tool_name in seen_names:
                continue
            tool_definition = global_tool_storage.get_tool(tool_name)
            if not tool_definition:
                continue
            tools.append(build_dynamic_tool(tool_definition))
            seen_names.add(tool_name)
            dynamic_tool_names.add(tool_name)

    for tool in build_subagent_builtin_tools(capability_profile=profile, sandbox=sandbox):
        if tool.name in seen_names:
            continue
        tools.append(tool)
        seen_names.add(tool.name)

    for tool in _resolve_capability_management_tools(
        capability_profile=profile,
        local_tool_storage=tool_storage,
        global_tool_storage=global_tool_storage,
        agent_storage=agent_storage,
        llm_factory=llm_factory,
        control_policy=control_policy,
        approval_queue=approval_queue,
        project_paths=project_paths,
    ):
        if tool.name in seen_names:
            continue
        tools.append(tool)
        seen_names.add(tool.name)

    return filter_tools_for_policy(
        tools=tools,
        control_policy=effective_policy,
        dynamic_tool_names=dynamic_tool_names,
    )


@dataclass(frozen=True)
class SubAgentRuntimeConfig:
    recursion_limit: int = 80
    excluded_state_keys: frozenset[str] = field(default_factory=lambda: EXCLUDED_SUBAGENT_STATE_KEYS)


@dataclass(frozen=True)
class SubAgentInvocationResult:
    status: str
    response: str
    agent_name: str
    role: str
    success: bool
    state_update: dict[str, Any]
    tool_names: list[str]
    thread_id: str
    sandbox: dict[str, Any]
    approval_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "response": self.response,
            "agent_name": self.agent_name,
            "role": self.role,
            "success": self.success,
            "state_update": self.state_update,
            "tool_names": self.tool_names,
            "thread_id": self.thread_id,
            "sandbox": self.sandbox,
            "approval_id": self.approval_id,
        }


class SubAgentRuntime:
    """Thin wrapper around a compiled LangChain subagent graph."""

    def __init__(
        self,
        *,
        graph: Any,
        definition: AgentDefinition,
        tool_names: list[str],
        control_policy: AgentControlPolicy,
        sandbox: SubagentSandbox,
        checkpoint_bundle: SubagentCheckpointBundle,
        approval_queue: ApprovalQueue | None = None,
        runtime_config: SubAgentRuntimeConfig | None = None,
    ):
        self.graph = graph
        self.definition = definition
        self.tool_names = tool_names
        self.control_policy = control_policy
        self.sandbox = sandbox
        self.checkpoint_bundle = checkpoint_bundle
        self.approval_queue = approval_queue or ApprovalQueue()
        self.runtime_config = runtime_config or SubAgentRuntimeConfig()

    def invoke(
        self,
        task: str,
        context: str = "",
        thread_id: str = "default",
        parent_state: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        subagent_state = {
            "messages": [{"role": "user", "content": build_subagent_prompt(task, context)}],
            **filter_subagent_state(parent_state),
        }

        config = {
            "configurable": {"thread_id": thread_id},
            "recursion_limit": self.runtime_config.recursion_limit,
        }
        result = self.graph.invoke(subagent_state, config=config)
        pending = self._register_tool_approval(result, thread_id=thread_id, config=config)
        if pending is not None:
            return SubAgentInvocationResult(
                status="waiting_approval",
                response=(f"子智能体 '{self.definition.name}' 已暂停，等待人工审批（{pending.approval_id}）。"),
                agent_name=self.definition.name,
                role=self.definition.role,
                success=False,
                state_update={},
                tool_names=self.tool_names,
                thread_id=thread_id,
                sandbox=self.sandbox.to_dict(),
                approval_id=pending.approval_id,
            ).to_dict()

        final_messages = result.get("messages", [])
        response_text = final_messages[-1].content if final_messages else ""
        success = getattr(final_messages[-1], "status", "success") != "error" if final_messages else True
        state_update = {
            key: value for key, value in result.items() if key not in self.runtime_config.excluded_state_keys
        }

        return SubAgentInvocationResult(
            status="completed",
            response=response_text,
            agent_name=self.definition.name,
            role=self.definition.role,
            success=success,
            state_update=state_update,
            tool_names=self.tool_names,
            thread_id=thread_id,
            sandbox=self.sandbox.to_dict(),
        ).to_dict()

    def resume_approval(
        self,
        *,
        approval_id: str,
        thread_id: str,
        approved: bool,
        note: str = "",
    ) -> dict[str, Any]:
        request = self.approval_queue.get_request(approval_id)
        approval = approval_interrupt_from_metadata(
            request.metadata if request is not None else None,
            fallback_scope=f"subagent:{self.definition.name}",
        )
        if approval is None:
            raise ValueError(f"无法从审批请求 '{approval_id}' 恢复子智能体工具审批")

        config = {
            "configurable": {"thread_id": thread_id},
            "recursion_limit": self.runtime_config.recursion_limit,
        }
        return self._resume_tool_approval(
            approval=approval,
            thread_id=thread_id,
            config=config,
            approved=approved,
            note=note,
        )

    def _register_tool_approval(
        self,
        response: dict[str, Any],
        *,
        thread_id: str,
        config: dict[str, Any],
    ):
        interrupts = extract_tool_approval_interrupts(
            response,
            scope=f"subagent:{self.definition.name}",
        )
        if not interrupts:
            return None
        approval = interrupts[0]
        return create_tool_approval_request(
            approval_queue=self.approval_queue,
            approval=approval,
            thread_id=thread_id,
            target=f"subagent:{self.definition.name}",
            callback=lambda approved, note: self._resume_tool_approval(
                approval=approval,
                thread_id=thread_id,
                config=config,
                approved=approved,
                note=note,
            ),
        )

    def _resume_tool_approval(
        self,
        *,
        approval,
        thread_id: str,
        config: dict[str, Any],
        approved: bool,
        note: str,
    ) -> dict[str, Any]:
        response = self.graph.invoke(
            build_tool_approval_resume_command(approval, approved=approved, note=note),
            config=config,
        )
        pending = self._register_tool_approval(response, thread_id=thread_id, config=config)
        if pending is not None:
            return {
                "status": "waiting_approval",
                "response": (
                    f"子智能体 '{self.definition.name}' 继续执行后再次暂停，等待审批（{pending.approval_id}）。"
                ),
                "approval_id": pending.approval_id,
                "thread_id": thread_id,
            }
        final_messages = response.get("messages", [])
        response_text = final_messages[-1].content if final_messages else ""
        success = getattr(final_messages[-1], "status", "success") != "error" if final_messages else approved
        return {
            "status": "completed",
            "response": response_text,
            "success": success,
            "thread_id": thread_id,
        }

    def close(self) -> None:
        self.checkpoint_bundle.close()


def _default_llm(model: str, temperature: float) -> BaseChatModel:
    """Fallback LLM creation when no factory is provided."""
    from .model_resolver import ModelProviderError, resolve_model

    try:
        return resolve_model(model, temperature=temperature).model
    except ModelProviderError:
        from langchain_openai import ChatOpenAI

        return ChatOpenAI(model=model, temperature=temperature)


def create_sub_agent_instance(
    *,
    agent_def: AgentDefinition,
    tool_storage: ToolStorage | None = None,
    global_tool_storage: ToolStorage | None = None,
    agent_storage: Any = None,
    llm_factory: Any = None,
    control_policy: AgentControlPolicy | None = None,
    approval_queue: ApprovalQueue | None = None,
    project_paths: ProjectPaths | None = None,
    runtime_config: SubAgentRuntimeConfig | None = None,
) -> SubAgentRuntime:
    """Create an isolated subagent runtime with its own checkpoint scope."""
    from langchain.agents import create_agent

    profile = AgentCapabilityProfile.from_value(agent_def.capability_profile)
    middleware_profile = AgentMiddlewareProfile.from_value(agent_def.middleware_profile)
    subagent_policy = derive_subagent_control_policy(base_policy=control_policy, capability_profile=profile)
    llm = (
        llm_factory(model=agent_def.model, temperature=agent_def.temperature)
        if llm_factory
        else _default_llm(model=agent_def.model, temperature=agent_def.temperature)
    )
    sandbox = build_subagent_sandbox(
        agent_name=agent_def.name,
        capability_profile=profile,
        project_paths=project_paths,
    )
    tools = resolve_subagent_tools(
        agent_def=agent_def,
        tool_storage=tool_storage,
        global_tool_storage=global_tool_storage,
        agent_storage=agent_storage,
        llm_factory=llm_factory,
        control_policy=control_policy,
        approval_queue=approval_queue,
        project_paths=project_paths,
    )
    tool_names = [tool.name for tool in tools]
    known_dynamic_tool_names = set(agent_def.tools)
    if tool_storage is not None:
        known_dynamic_tool_names.update(tool_storage.list_tools().keys())
    middleware = DynamicToolMiddleware(
        tool_storage=tool_storage,
        control_policy=subagent_policy,
        approval_queue=approval_queue,
        approval_scope=f"subagent:{agent_def.name}",
    )
    middleware.set_base_tools(tools)
    middleware.set_known_dynamic_tools(sorted(known_dynamic_tool_names))
    checkpoint_bundle = build_subagent_checkpointer(
        agent_name=agent_def.name,
        project_paths=project_paths,
    )
    graph = create_agent(
        model=llm,
        tools=tools,
        system_prompt=agent_def.system_prompt,
        middleware=build_subagent_langchain_middleware(
            definition=agent_def,
            sandbox=sandbox,
            capability_profile=profile,
            middleware_profile=middleware_profile,
            effective_policy=subagent_policy,
            tool_middleware=middleware,
        ),
        checkpointer=checkpoint_bundle.checkpointer,
    )
    return SubAgentRuntime(
        graph=graph,
        definition=agent_def,
        tool_names=tool_names,
        control_policy=subagent_policy,
        sandbox=sandbox,
        checkpoint_bundle=checkpoint_bundle,
        approval_queue=approval_queue,
        runtime_config=runtime_config,
    )


def _resolve_capability_management_tools(
    *,
    capability_profile: AgentCapabilityProfile,
    local_tool_storage: ToolStorage | None,
    global_tool_storage: ToolStorage | None,
    agent_storage: Any,
    llm_factory: Any,
    control_policy: AgentControlPolicy | None,
    approval_queue: ApprovalQueue | None,
    project_paths: ProjectPaths | None,
) -> list[Any]:
    tools: list[Any] = []

    if capability_profile.allow_local_tool_creation or capability_profile.allow_template_tools:
        from .tool_creator import ListTemplatesTool, TemplateToolCreator, ToolCreatorTool

        if capability_profile.allow_local_tool_creation and local_tool_storage is not None:
            tools.append(ToolCreatorTool(storage=local_tool_storage, agent_storage=None))
        if capability_profile.allow_template_tools and local_tool_storage is not None:
            tools.append(TemplateToolCreator(storage=local_tool_storage, agent_storage=None))
            tools.append(ListTemplatesTool())

    if capability_profile.allow_local_tool_removal and local_tool_storage is not None:
        from .tool_creator import RemoveToolTool

        tools.append(RemoveToolTool(storage=local_tool_storage))

    if capability_profile.allow_agent_creation and agent_storage is not None:
        from .agent_creator import AgentCreatorTool

        tools.append(AgentCreatorTool(agent_storage=agent_storage))

    if capability_profile.allow_list_agents and agent_storage is not None:
        from .agent_creator import ListAgentsTool

        tools.append(ListAgentsTool(agent_storage=agent_storage))

    if capability_profile.allow_agent_delegation and agent_storage is not None and llm_factory is not None:
        from .agent_creator import DelegateToAgentTool

        tools.append(
            DelegateToAgentTool(
                agent_storage=agent_storage,
                llm_factory=llm_factory,
                control_policy=control_policy,
                global_tool_storage=global_tool_storage,
                approval_queue=approval_queue,
                project_paths=project_paths,
            )
        )

    if capability_profile.allow_agent_removal and agent_storage is not None:
        from .agent_creator import RemoveAgentTool

        tools.append(RemoveAgentTool(agent_storage=agent_storage))

    return tools
