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

from core.assets.agents.agent_creator import get_agent_creator_tools
from core.assets.agents.agent_services import invoke_persisted_agent
from core.assets.agents.agent_storage import AgentStorage
from core.assets.agents.subagent_registry import SubagentRegistry
from core.assets.apps import (
    AppManager,
    AppOrchestrationRegistry,
    get_app_creator_tools,
    get_app_verifier_tools,
)
from core.assets.apps.app_manager_registry import set_shared_app_manager
from core.assets.apps.app_orchestration_tools import get_app_orchestration_tools
from core.assets.skills.skill_marketplace import SkillMarketplace, get_marketplace_tools
from core.assets.skills.skill_registry import SkillRegistry
from core.assets.tools import (
    DynamicToolMiddleware,
    ToolChainExecutor,
    ToolStorage,
    get_dynamic_tools,
    get_template_prompt_section,
    get_tool_chain_tools,
    get_tool_creator_tools,
)
from core.assets.tools.clarification_tool import get_clarification_tools
from core.assets.workflows import PyFlowEngine, TaskScheduler, get_pyflow_tools
from core.systems.bus import (
    CapabilityBus,
    CapabilityRegistry,
    get_capability_bus_tools,
    get_capability_registry_tools,
)
from core.systems.context.context_manager import ContextWindowManager
from core.systems.eval.eval_framework import EvalFramework, get_eval_tools
from core.systems.execution import get_execution_loop_tools
from core.systems.governance import AgentControlPolicy, ApprovalQueue
from core.systems.integration import ChannelManager, MCPHub
from core.systems.memory import MemoryManager, SemanticMemoryManager
from core.systems.memory.memory_tools import get_memory_tools
from core.systems.middleware.agent_middleware_factory import build_root_langchain_middleware
from core.systems.middleware.middleware_stack import MiddlewareStack
from core.systems.runtime.backend_protocol import LocalSandboxBackend
from core.systems.runtime.config_impl import (
    get_channel_routes_config,
    get_channels_config,
    get_extra_skill_sources,
)
from core.systems.runtime.daemon import BackgroundDaemon, SessionReaper
from core.systems.runtime.model_failover import create_failover_model
from core.systems.runtime.model_resolver import ModelProviderError, resolve_model
from core.systems.runtime.project_paths import ProjectPaths
from core.systems.runtime.prompts import build_static_system_prompt
from core.systems.runtime.runtime_capability_bundle import build_capability_runtime_bundle
from core.systems.runtime.workspace_manager import WorkspaceManager


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
    capability_registry: CapabilityRegistry
    middleware_stack: MiddlewareStack
    llm: BaseChatModel
    checkpointer: SqliteSaver
    middleware: DynamicToolMiddleware
    control_policy: AgentControlPolicy
    approval_queue: ApprovalQueue
    subagent_registry: SubagentRegistry
    daemon: BackgroundDaemon
    session_runtime: Any | None = None
    orchestration_registry: AppOrchestrationRegistry | None = None
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
    root_mode: str = "assistant",
    session_runtime: Any | None = None,
) -> PyBotRuntime:
    """Construct the shared runtime used by the root agent."""
    storage = ToolStorage(base_dir=str(paths.global_tools_dir))
    agent_storage = AgentStorage(base_dir=str(paths.agents_dir))
    backend = LocalSandboxBackend(root_dir=str(paths.workspace_dir))

    workspace = WorkspaceManager(str(paths.workspace_dir))
    from core.assets.skills.skill_sources import SkillSource

    extra_sources_cfg = get_extra_skill_sources()
    skill_sources: list[str | SkillSource] | None = None
    if extra_sources_cfg:
        skill_sources = [
            SkillSource(name=e.get("name", f"external_{i}"), path=e["path"], writable=False)
            for i, e in enumerate(extra_sources_cfg)
        ]
        skill_sources.append(SkillSource(name="workspace", path=str(paths.skills_dir), writable=True))
    skill_registry = SkillRegistry(
        str(paths.skills_dir) if skill_sources is None else None,
        skill_sources=skill_sources,
    )

    scheduler = TaskScheduler(str(paths.workspace_dir))
    app_manager = AppManager(str(paths.apps_dir), project_paths=paths)
    set_shared_app_manager(app_manager)

    skill_marketplace = SkillMarketplace(str(paths.workspace_dir))
    mcp_hub = MCPHub(str(paths.workspace_dir))
    channel_manager = ChannelManager(
        str(paths.workspace_dir),
        channel_configs=get_channels_config(),
        channel_routes=get_channel_routes_config(),
    )

    capability_bundle = build_capability_runtime_bundle(
        paths=paths,
        thread_id=thread_id,
        summarize_callback=summarize_callback,
        tool_callback=tool_callback,
        agent_callback=agent_callback,
        delegate_callback=delegate_callback,
        skill_registry=skill_registry,
        skill_marketplace=skill_marketplace,
        app_manager=app_manager,
        agent_storage=agent_storage,
        control_config=control_config,
        approval_queue=approval_queue,
        session_runtime=session_runtime,
    )
    resolved_approval_queue = capability_bundle.approval_queue
    pyflow_engine = capability_bundle.pyflow_engine
    tool_chain = capability_bundle.tool_chain
    eval_framework = capability_bundle.eval_framework
    context_manager = capability_bundle.context_manager
    capability_bus = capability_bundle.capability_bus
    capability_registry = capability_bundle.capability_registry
    control_policy = capability_bundle.control_policy
    subagent_registry = capability_bundle.subagent_registry
    middleware_stack = capability_bundle.middleware_stack

    knowledge_tools_list: list[Any] = []
    memory: MemoryManager | SemanticMemoryManager = MemoryManager(str(paths.workspace_dir))
    try:
        from core.systems.runtime.config_impl import get_rag_config

        rag_cfg = get_rag_config()
        if rag_cfg.get("enabled"):
            from core.systems.knowledge.document_pipeline import ChunkConfig, DocumentPipeline
            from core.systems.knowledge.embedding_resolver import resolve_embeddings
            from core.systems.knowledge.knowledge_tools import get_knowledge_tools
            from core.systems.knowledge.vector_store import create_vector_store

            persist_dir = rag_cfg.get("persist_dir") or str(paths.workspace_dir / "vector_store")
            embedding_spec = rag_cfg.get("embedding_model")
            if isinstance(embedding_spec, str) and rag_cfg.get("embedding_batch_size"):
                provider_name, sep, model_name = embedding_spec.partition(":")
                if sep:
                    embedding_spec = {
                        "provider": provider_name,
                        "model": model_name,
                        "batch_size": rag_cfg.get("embedding_batch_size", 32),
                    }
            emb_fn = resolve_embeddings(embedding_spec, api_key=api_key)
            vs = create_vector_store(
                backend=rag_cfg.get("backend", "chroma"),
                persist_dir=persist_dir,
                embedding_function=emb_fn,
            )
            memory = SemanticMemoryManager(
                workspace_dir=str(paths.workspace_dir),
                vector_store=vs,
                search_strategy=rag_cfg.get("search_strategy", "vector"),
                keyword_weight=float(rag_cfg.get("hybrid_keyword_weight", 0.35)),
                vector_weight=float(rag_cfg.get("hybrid_vector_weight", 0.65)),
                mmr_enabled=bool(rag_cfg.get("mmr_enabled", False)),
                mmr_lambda=float(rag_cfg.get("mmr_lambda", 0.7)),
                temporal_decay_enabled=bool(rag_cfg.get("temporal_decay_enabled", False)),
                temporal_half_life_days=float(rag_cfg.get("temporal_half_life_days", 30.0)),
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
    if isinstance(memory, SemanticMemoryManager):
        memory._llm = llm
    conn = sqlite3.connect(str(paths.checkpoints_db), check_same_thread=False)
    checkpointer = SqliteSaver(conn)
    middleware = DynamicToolMiddleware(
        tool_storage=storage,
        control_policy=control_policy,
        approval_queue=resolved_approval_queue,
        approval_scope=f"root:{thread_id}",
        allowed_path_roots=[
            str(paths.runtime_root_dir),
            str(paths.workspace_dir),
            str(paths.tools_workspace_dir),
        ],
        event_context_resolver=lambda: {
            "thread_id": thread_id,
            "run_id": "",
            "current_agent_name": "root",
            "approval_scope": f"root:{thread_id}",
            "root_mode": root_mode,
        },
    )

    daemon = BackgroundDaemon()
    reaper = SessionReaper(
        lease_manager=pyflow_engine.node_runtime.operator.lease_manager,
        task_queue=None,  # We'll attach the global task queue if needed
    )
    daemon.add_job("session_reaper", 60.0, reaper.run_reap_cycle)

    from core.systems.runtime.admin_watcher import AdminWatcherDaemon

    AdminWatcherDaemon(
        llm=llm,
        daemon=daemon,
        workspace_dir=paths.workspace_dir,
        interval_sec=120.0,  # Run every 2 minutes for telemetry analysis
    )

    daemon.start()

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
        capability_registry=capability_registry,
        middleware_stack=middleware_stack,
        llm=llm,
        checkpointer=checkpointer,
        middleware=middleware,
        control_policy=control_policy,
        approval_queue=resolved_approval_queue,
        subagent_registry=subagent_registry,
        daemon=daemon,
        session_runtime=session_runtime,
        knowledge_tools=knowledge_tools_list,
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
            subagent_registry=runtime.subagent_registry,
            runtime_context={"current_agent_name": "root", "depth": 0},
        )
        creator_tools.extend(agent_tools)
        tool_groups.append(("智能体工具", len(agent_tools)))

    app_tools = get_app_creator_tools(llm=llm_factory(None, None))
    creator_tools.extend(app_tools)
    tool_groups.append(("应用工具", len(app_tools)))

    from core.assets.apps.marketplace_tools import get_app_marketplace_tools

    app_marketplace_tools = get_app_marketplace_tools()
    creator_tools.extend(app_marketplace_tools)
    tool_groups.append(("应用集市工具", len(app_marketplace_tools)))

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

    if runtime.orchestration_registry is not None:
        orch_tools = get_app_orchestration_tools(runtime.orchestration_registry)
        creator_tools.extend(orch_tools)
        tool_groups.append(("编排工具", len(orch_tools)))

    skill_tools = runtime.skill_registry.get_active_tools()
    if skill_tools:
        creator_tools.extend(skill_tools)
        tool_groups.append(("技能工具", len(skill_tools)))

    runtime.capability_registry.refresh_local_index(tools=creator_tools)

    runtime.channel_manager.set_agent_callback(chat_callback)

    system_prompt = build_static_system_prompt(
        template_section=get_template_prompt_section(),
        root_mode=root_mode,
    )

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
    response_format: Any | None = None,
) -> Any:
    """Build the root LangChain agent instance.

    Args:
        response_format: Optional Pydantic model or ResponseFormat to enforce
            structured output from the agent. When provided, the agent's
            final response will be validated against this schema.
    """
    from core.systems.middleware.summarization_middleware import SummarizationConfig

    summ_config = SummarizationConfig(
        offload_dir=str(getattr(runtime, "_conversation_offload_dir", "")),
        thread_id=getattr(runtime, "_thread_id", "default"),
    )
    session_compaction_callback = None
    artifacts_provider = None
    if getattr(runtime, "session_runtime", None) is not None:
        _sr = runtime.session_runtime
        _tid = str(getattr(runtime, "_thread_id", "default"))

        def _record_session_compaction(payload: dict[str, Any]) -> None:
            _sr.record_external_compaction(
                thread_id=str(payload.get("thread_id", _tid)),
                session_key=_sr.session_key_for_thread(
                    str(payload.get("thread_id", _tid))
                ),
                summary=str(payload.get("summary", "")),
                source=str(payload.get("source", "middleware.summarization")),
                reason=str(payload.get("reason", "conversation_compaction")),
                message_count=int(payload.get("message_count", 0) or 0),
                recent_window=int(payload.get("recent_window", 0) or 0),
                offload_path=str(payload.get("offload_path", "")),
                root_mode=str(getattr(runtime, "_root_mode", "assistant")),
            )

        session_compaction_callback = _record_session_compaction

        def _get_artifacts() -> dict[str, Any] | None:
            sk = _sr.session_key_for_thread(_tid)
            if not sk:
                return None
            return _sr.get_compiled_artifacts(sk)

        artifacts_provider = _get_artifacts

    kwargs: dict[str, Any] = dict(
        model=runtime.llm,
        tools=assembly.all_tools,
        system_prompt=assembly.system_prompt,
        middleware=build_root_langchain_middleware(
            runtime=runtime,
            summarize_fn=getattr(runtime.context_manager, "config", None)
            and runtime.context_manager.config.summarize_callback,
            summarization_config=summ_config,
            session_compaction_callback=session_compaction_callback,
            artifacts_provider=artifacts_provider,
        ),
        checkpointer=runtime.checkpointer,
    )
    if response_format is not None:
        kwargs["response_format"] = response_format
    return create_agent(**kwargs)


def delegate_to_sub_agent(
    *,
    agent_storage: AgentStorage,
    global_tool_storage: ToolStorage | None = None,
    llm_factory: Callable[[str | None, float | None], BaseChatModel],
    control_policy: AgentControlPolicy | None = None,
    approval_queue: ApprovalQueue | None = None,
    project_paths: ProjectPaths | None = None,
    subagent_registry: SubagentRegistry | None = None,
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
        subagent_registry=subagent_registry,
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
    subagent_registry: SubagentRegistry | None = None,
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
        subagent_registry=subagent_registry,
        agent_name=agent_name,
        task=task,
        context=context,
        thread_id=resolved_thread_id,
        parent_agent_name="root",
        parent_depth=0,
    )
