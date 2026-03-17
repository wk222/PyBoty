"""Bootstrap helpers for assembling the root PyBot runtime."""

from __future__ import annotations

import sqlite3
import time as time_module
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from langchain.agents import create_agent
from langchain_core.language_models import BaseChatModel
from langchain_openai import ChatOpenAI
from langgraph.checkpoint.sqlite import SqliteSaver

from .agent_control import AgentControlPolicy
from .agent_creator import get_agent_creator_tools
from .agent_middleware_factory import build_root_langchain_middleware
from .agent_services import invoke_persisted_agent
from .agent_storage import AgentStorage
from .app_creator import get_app_creator_tools
from .app_manager import AppManager
from .app_manager_registry import set_shared_app_manager
from .app_verifier import get_app_verifier_tools
from .approval_queue import ApprovalQueue
from .backend_protocol import LocalSandboxBackend
from .capability_bus import CapabilityBus, get_capability_bus_tools
from .channel_manager import ChannelManager
from .clarification_tool import get_clarification_tools
from .config import get_agent_control_config
from .context_manager import ContextConfig, ContextWindowManager
from .eval_framework import EvalFramework, get_eval_tools
from .execution_loop import get_execution_loop_tools
from .mcp_hub import MCPHub
from .memory_manager import MemoryManager
from .middleware_stack import MiddlewareStack
from .model_failover import create_failover_model
from .model_resolver import ModelProviderError, resolve_model
from .project_paths import ProjectPaths
from .prompts import build_static_system_prompt
from .pyflow_engine import PyFlowEngine
from .skill_marketplace import SkillMarketplace, get_marketplace_tools
from .skill_registry import SkillRegistry
from .task_scheduler import TaskScheduler
from .tool_chain import ToolChainExecutor, get_tool_chain_tools
from .tool_creator import get_dynamic_tools, get_tool_creator_tools
from .tool_middleware import DynamicToolMiddleware
from .tool_storage import ToolStorage
from .tool_templates import get_template_prompt_section
from .workflow_tools import get_pyflow_tools
from .workspace_manager import WorkspaceManager


@dataclass
class PyBotRuntime:
    """Runtime services shared by the root PyBot instance."""

    storage: ToolStorage
    agent_storage: AgentStorage
    backend: LocalSandboxBackend
    workspace: WorkspaceManager
    memory: MemoryManager
    skill_registry: SkillRegistry
    scheduler: TaskScheduler
    app_manager: AppManager
    skill_marketplace: SkillMarketplace
    mcp_hub: MCPHub
    channel_manager: ChannelManager
    pyflow_engine: PyFlowEngine
    tool_chain: ToolChainExecutor
    eval_framework: EvalFramework
    context_manager: ContextWindowManager
    capability_bus: CapabilityBus
    middleware_stack: MiddlewareStack
    llm: BaseChatModel
    checkpointer: SqliteSaver
    middleware: DynamicToolMiddleware
    control_policy: AgentControlPolicy
    approval_queue: ApprovalQueue
    knowledge_tools: list[Any] = field(default_factory=list)


@dataclass
class ToolAssembly:
    """Resolved tool set and prompt for the root agent."""

    creator_tools: list[Any]
    dynamic_tools: list[Any]
    all_tools: list[Any]
    system_prompt: str
    tool_groups: list[tuple[str, int]]


def create_llm_client(
    *,
    model: str,
    temperature: float,
    api_key: str | None = None,
    base_url: str | None = None,
    provider: str | None = None,
    fallback_configs: list[dict[str, Any]] | None = None,
) -> BaseChatModel:
    """Create an LLM client via multi-provider resolver with optional failover.

    Supports "provider:model" format (e.g. "anthropic:claude-sonnet-4-20250514")
    as well as plain model names with explicit api_base for OpenAI-compatible endpoints.
    """
    spec: str | dict[str, Any]
    if provider and provider != "openai":
        spec = f"{provider}:{model}"
    elif base_url:
        spec = {"provider": "openai", "model": model, "api_base": base_url}
    else:
        spec = model

    try:
        resolved = resolve_model(spec, temperature=temperature, api_key=api_key, base_url=base_url)
        primary = resolved.model
    except ModelProviderError:
        kwargs: dict[str, Any] = {"model": model, "temperature": temperature}
        if api_key:
            kwargs["api_key"] = api_key
        if base_url:
            kwargs["base_url"] = base_url
        primary = ChatOpenAI(**kwargs)

    if not fallback_configs:
        return primary

    fallbacks: list[BaseChatModel] = []
    for fb_cfg in fallback_configs:
        try:
            fb_resolved = resolve_model(fb_cfg, temperature=temperature)
            fallbacks.append(fb_resolved.model)
        except ModelProviderError as exc:
            import logging

            logging.getLogger(__name__).warning("Skipping fallback model: %s", exc)
    return create_failover_model(primary, fallbacks)


def build_runtime(
    *,
    paths: ProjectPaths,
    model: str,
    temperature: float,
    api_key: str | None,
    base_url: str | None,
    provider: str | None = None,
    fallback_configs: list[dict[str, Any]] | None = None,
    thread_id: str,
    control_config: dict[str, Any] | None,
    approval_queue: ApprovalQueue | None,
    summarize_callback: Callable[[str], str],
    tool_callback: Callable[[str, dict[str, Any]], Any],
    agent_callback: Callable[[str], str],
    delegate_callback: Callable[[str, str, str], Any],
) -> PyBotRuntime:
    """Construct the shared runtime used by the root agent."""
    storage = ToolStorage(base_dir=str(paths.global_tools_dir))
    agent_storage = AgentStorage(base_dir=str(paths.agents_dir))
    backend = LocalSandboxBackend(root_dir=str(paths.workspace_dir))

    workspace = WorkspaceManager(str(paths.workspace_dir))
    memory = MemoryManager(str(paths.workspace_dir))
    skill_registry = SkillRegistry(str(paths.skills_dir))
    scheduler = TaskScheduler(str(paths.workspace_dir))
    app_manager = AppManager(str(paths.apps_dir), project_paths=paths)
    set_shared_app_manager(app_manager)

    skill_marketplace = SkillMarketplace(str(paths.workspace_dir))
    mcp_hub = MCPHub(str(paths.workspace_dir))
    channel_manager = ChannelManager(str(paths.workspace_dir))

    resolved_approval_queue = approval_queue or ApprovalQueue(storage_path=paths.approvals_file)
    pyflow_engine = PyFlowEngine(str(paths.workspace_dir), approval_queue=resolved_approval_queue)
    pyflow_engine.configure_callbacks(
        tool_callback=tool_callback,
        agent_callback=agent_callback,
        delegate_callback=delegate_callback,
    )

    tool_chain = ToolChainExecutor()
    tool_chain.set_tool_callback(tool_callback)

    eval_framework = EvalFramework(str(paths.workspace_dir))
    eval_framework.set_agent_callback(summarize_callback)

    context_manager = ContextWindowManager(
        ContextConfig(
            max_tokens=12000,
            summarize_callback=summarize_callback,
            offload_dir=str(paths.conversation_offload_dir),
            thread_id=thread_id,
        )
    )
    capability_bus = CapabilityBus(str(paths.workspace_dir))
    control_policy = AgentControlPolicy.from_config(control_config or get_agent_control_config())

    middleware_stack = MiddlewareStack()  # empty legacy stack; all middleware now in LangChain pipeline

    knowledge_tools_list: list[Any] = []
    try:
        from .config import get_rag_config

        rag_cfg = get_rag_config()
        if rag_cfg.get("enabled"):
            from .document_pipeline import ChunkConfig, DocumentPipeline
            from .embedding_resolver import resolve_embeddings
            from .knowledge_tools import get_knowledge_tools
            from .vector_store import create_vector_store

            persist_dir = rag_cfg.get("persist_dir") or str(paths.workspace_dir / "vector_store")
            emb_fn = resolve_embeddings(rag_cfg.get("embedding_model"), api_key=api_key)
            vs = create_vector_store(
                backend=rag_cfg.get("backend", "chroma"),
                persist_dir=persist_dir,
                embedding_function=emb_fn,
            )
            pipeline = DocumentPipeline(
                vs,
                chunk_config=ChunkConfig(
                    chunk_size=rag_cfg.get("chunk_size", 1000),
                    chunk_overlap=rag_cfg.get("chunk_overlap", 200),
                ),
            )
            knowledge_tools_list = get_knowledge_tools(vs, pipeline)
    except Exception as exc:
        import logging

        logging.getLogger(__name__).warning("RAG initialization skipped: %s", exc)

    llm = create_llm_client(
        model=model,
        temperature=temperature,
        api_key=api_key,
        base_url=base_url,
        provider=provider,
        fallback_configs=fallback_configs,
    )
    conn = sqlite3.connect(str(paths.checkpoints_db), check_same_thread=False)
    checkpointer = SqliteSaver(conn)
    middleware = DynamicToolMiddleware(
        tool_storage=storage,
        control_policy=control_policy,
        approval_queue=resolved_approval_queue,
        approval_scope=f"root:{thread_id}",
    )

    return PyBotRuntime(
        storage=storage,
        agent_storage=agent_storage,
        backend=backend,
        workspace=workspace,
        memory=memory,
        skill_registry=skill_registry,
        scheduler=scheduler,
        app_manager=app_manager,
        skill_marketplace=skill_marketplace,
        mcp_hub=mcp_hub,
        channel_manager=channel_manager,
        pyflow_engine=pyflow_engine,
        tool_chain=tool_chain,
        eval_framework=eval_framework,
        context_manager=context_manager,
        capability_bus=capability_bus,
        middleware_stack=middleware_stack,
        llm=llm,
        checkpointer=checkpointer,
        middleware=middleware,
        control_policy=control_policy,
        approval_queue=resolved_approval_queue,
        knowledge_tools=knowledge_tools_list,
    )


def assemble_primary_tools(
    *,
    runtime: PyBotRuntime,
    paths: ProjectPaths,
    enable_agent_creation: bool,
    llm_factory: Callable[[str | None, float | None], BaseChatModel],
    chat_callback: Callable[[str], str],
) -> ToolAssembly:
    """Resolve root-agent tools and prompt extensions."""
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
        )
        creator_tools.extend(agent_tools)
        tool_groups.append(("智能体工具", len(agent_tools)))

    app_tools = get_app_creator_tools()
    creator_tools.extend(app_tools)
    tool_groups.append(("应用工具", len(app_tools)))

    clarification_tools = get_clarification_tools()
    creator_tools.extend(clarification_tools)
    tool_groups.append(("澄清工具", len(clarification_tools)))

    verifier_tools = get_app_verifier_tools()
    creator_tools.extend(verifier_tools)
    tool_groups.append(("验证工具", len(verifier_tools)))

    marketplace_tools = get_marketplace_tools(runtime.skill_marketplace)
    creator_tools.extend(marketplace_tools)
    tool_groups.append(("市场工具", len(marketplace_tools)))

    pyflow_tools = get_pyflow_tools(runtime.pyflow_engine)
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

    mcp_tools = runtime.mcp_hub.get_tools()
    if mcp_tools:
        creator_tools.extend(mcp_tools)
        tool_groups.append(("MCP 工具", len(mcp_tools)))

    if runtime.knowledge_tools:
        creator_tools.extend(runtime.knowledge_tools)
        tool_groups.append(("知识检索", len(runtime.knowledge_tools)))

    runtime.capability_bus.auto_register_tools(creator_tools)
    runtime.capability_bus.auto_register_workflows(runtime.pyflow_engine)
    runtime.capability_bus.auto_register_apps(runtime.app_manager)
    runtime.capability_bus.auto_register_skills(runtime.skill_registry)
    runtime.capability_bus.auto_register_agents(runtime.agent_storage)

    skill_tools = runtime.skill_registry.get_active_tools()
    if skill_tools:
        creator_tools.extend(skill_tools)
        tool_groups.append(("技能工具", len(skill_tools)))

    runtime.channel_manager.set_agent_callback(chat_callback)

    system_prompt = build_static_system_prompt(template_section=get_template_prompt_section())

    dynamic_tools = get_dynamic_tools(runtime.storage)
    all_tools = creator_tools + dynamic_tools
    runtime.middleware._tool_storage = runtime.storage
    runtime.middleware.set_base_tools(creator_tools)

    return ToolAssembly(
        creator_tools=creator_tools,
        dynamic_tools=dynamic_tools,
        all_tools=all_tools,
        system_prompt=system_prompt,
        tool_groups=tool_groups,
    )


def create_root_agent(
    *,
    runtime: PyBotRuntime,
    assembly: ToolAssembly,
) -> Any:
    """Build the root LangChain agent instance."""
    from .summarization_middleware import SummarizationConfig

    summ_config = SummarizationConfig(
        offload_dir=str(getattr(runtime, "_conversation_offload_dir", "")),
        thread_id=getattr(runtime, "_thread_id", "default"),
    )
    return create_agent(
        model=runtime.llm,
        tools=assembly.all_tools,
        system_prompt=assembly.system_prompt,
        middleware=build_root_langchain_middleware(
            runtime=runtime,
            summarize_fn=getattr(runtime.context_manager, "config", None)
            and runtime.context_manager.config.summarize_callback,
            summarization_config=summ_config,
        ),
        checkpointer=runtime.checkpointer,
    )


def delegate_to_sub_agent(
    *,
    agent_storage: AgentStorage,
    global_tool_storage: ToolStorage | None = None,
    llm_factory: Callable[[str | None, float | None], BaseChatModel],
    control_policy: AgentControlPolicy | None = None,
    approval_queue: ApprovalQueue | None = None,
    project_paths: ProjectPaths | None = None,
    agent_name: str,
    task: str,
    context: str = "",
) -> str:
    """Invoke a named sub-agent with an isolated thread and tool storage."""
    response = invoke_sub_agent(
        agent_storage=agent_storage,
        global_tool_storage=global_tool_storage,
        llm_factory=llm_factory,
        control_policy=control_policy,
        approval_queue=approval_queue,
        project_paths=project_paths,
        agent_name=agent_name,
        task=task,
        context=context,
    )
    return response.get("response", "")


def invoke_sub_agent(
    *,
    agent_storage: AgentStorage,
    global_tool_storage: ToolStorage | None = None,
    llm_factory: Callable[[str | None, float | None], BaseChatModel],
    control_policy: AgentControlPolicy | None = None,
    approval_queue: ApprovalQueue | None = None,
    project_paths: ProjectPaths | None = None,
    agent_name: str,
    task: str,
    context: str = "",
    thread_id: str | None = None,
) -> dict[str, Any]:
    """Invoke a named sub-agent and return its structured response payload."""
    resolved_thread_id = thread_id or f"workflow_{agent_name}_{int(time_module.time())}"
    return invoke_persisted_agent(
        agent_storage=agent_storage,
        global_tool_storage=global_tool_storage,
        llm_factory=llm_factory,
        control_policy=control_policy,
        approval_queue=approval_queue,
        project_paths=project_paths,
        agent_name=agent_name,
        task=task,
        context=context,
        thread_id=resolved_thread_id,
    )
