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
    app_orchestration,
    app_runtime,
)
from core.assets.apps.app_manager_registry import set_shared_app_manager
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
from core.assets.workflows import PyFlowEngine, TaskScheduler, workflow_orchestration, workflow_runtime
from core.systems.bus import (
    CapabilityBus,
    CapabilityRegistry,
    get_capability_bus_tools,
    get_capability_registry_tools,
)
from core.systems.context import ContextWindowManager, WorkspaceViewService
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
    get_rag_config,
    get_trusted_settings,
)
from core.systems.runtime.daemon import BackgroundDaemon, SessionReaper
from core.systems.runtime.model_failover import create_failover_model
from core.systems.runtime.hooks_runtime import HookPhase, create_default_hooks_runtime
from core.systems.runtime.model_resolver import ModelProviderError, resolve_model
from core.systems.runtime.project_paths import ProjectPaths
from core.systems.runtime.prompts import build_static_system_prompt
from core.systems.runtime.runtime_capability_bundle import build_capability_runtime_bundle
from core.systems.runtime.subagent_isolation import build_root_isolation_projection
from core.systems.runtime.task_runtime import TaskRuntimeService
from core.systems.runtime.workspace_manager import WorkspaceManager
from core.systems.governance.permission_policy import PermissionControlPlane


@dataclass
class PyBotRuntime:
    """Runtime services shared by the root PyBot instance."""

    thread_id: str
    conversation_offload_dir: str
    root_mode: str
    storage: ToolStorage
    agent_storage: AgentStorage
    backend: LocalSandboxBackend
    workspace: WorkspaceManager
    workspace_view: WorkspaceViewService
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
    task_runtime: TaskRuntimeService
    hooks_runtime: Any | None = None
    trusted_settings: Any | None = None
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
    ask_user_fn: Callable[[str, str], bool] | None = None,
) -> PyBotRuntime:
    """Construct the shared runtime used by the root agent."""
    storage = ToolStorage(base_dir=str(paths.global_tools_dir))
    agent_storage = AgentStorage(base_dir=str(paths.agents_dir))
    backend = LocalSandboxBackend(root_dir=str(paths.workspace_dir))

    workspace = WorkspaceManager(str(paths.workspace_dir))
    workspace_view = WorkspaceViewService()
    task_runtime = TaskRuntimeService()
    hooks_runtime = create_default_hooks_runtime()
    user_settings_path = paths.runtime_root_dir / "config.json"
    legacy_settings_path = paths.root_dir / "config.json"
    if not user_settings_path.exists() and legacy_settings_path.exists():
        user_settings_path = legacy_settings_path
    trusted_settings = get_trusted_settings(
        path=str(user_settings_path),
        project_path=str(paths.root_dir / ".pybot" / "project.config.json"),
        system_path=str(paths.runtime_root_dir / "settings.system.json"),
    )
    effective_config = trusted_settings.effective
    from core.assets.skills.skill_sources import SkillSource

    extra_sources_cfg = get_extra_skill_sources(config=effective_config)
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

    scheduler = workflow_orchestration.scheduler_class(str(paths.workspace_dir))
    app_manager = app_runtime.manager_class(str(paths.apps_dir), project_paths=paths)
    set_shared_app_manager(app_manager)

    skill_marketplace = SkillMarketplace(str(paths.workspace_dir))
    mcp_hub = MCPHub(str(paths.workspace_dir))
    channel_manager = ChannelManager(
        str(paths.workspace_dir),
        channel_configs=get_channels_config(config=effective_config),
        channel_routes=get_channel_routes_config(config=effective_config),
    )

    resolved_control_config = control_config or effective_config.get("agent_control", {})
    permission_policy = PermissionControlPlane.from_trusted_settings(trusted_settings)

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
        control_config=resolved_control_config,
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
        rag_cfg = get_rag_config(config=effective_config)
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
        ask_user_fn=ask_user_fn,
        permission_policy=permission_policy,
        trusted_settings=trusted_settings,
        hooks_runtime=hooks_runtime,
        runtime_view_provider=lambda: (
            session_runtime.get_compiled_runtime_view(session_runtime.session_key_for_thread(thread_id))
            if session_runtime is not None and session_runtime.session_key_for_thread(thread_id)
            else None
        ),
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
        thread_id=str(thread_id),
        conversation_offload_dir=str(paths.conversation_offload_dir),
        root_mode=str(root_mode),
        storage=storage,
        agent_storage=agent_storage,
        backend=backend,
        workspace=workspace,
        workspace_view=workspace_view,
        task_runtime=task_runtime,
        trusted_settings=trusted_settings,
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
        hooks_runtime=hooks_runtime,
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

    from core.assets.tools.permission_tools import get_permission_tools

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

    from core.assets.tools.file_system_tools import get_file_system_tools
    fs_tools = get_file_system_tools(
        allowed_root=str(paths.workspace_dir),
        workspace_view=runtime.workspace_view,
    )
    creator_tools.extend(fs_tools)
    tool_groups.append(("文件系统工具", len(fs_tools)))

    from core.assets.tools.bash_tool import BashTool
    bash_tool = BashTool(allowed_root=str(paths.workspace_dir))
    creator_tools.append(bash_tool)
    tool_groups.append(("Shell 工具", 1))

    from core.assets.tools.web_fetch_tool import WebFetchTool
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
        system_prompt += f"\n\n{skill_summary}"

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
    from core.systems.bus.capability_tree import build_capability_tree_resume_projection
    from core.systems.memory.session_memory_extractor import (
        SessionMemoryConfig,
        SessionMemoryScheduler,
    )
    from core.systems.runtime.session.session_runtime_view import (
        compile_runtime_resume_view,
        merge_session_runtime_view,
    )
    from core.systems.runtime.projected_runtime_view import (
        build_projected_runtime_view,
        build_runtime_task_section,
    )

    _tid = str(getattr(runtime, "thread_id", getattr(runtime, "_thread_id", "default")))
    _offload_dir = str(
        getattr(
            runtime,
            "conversation_offload_dir",
            getattr(runtime, "_conversation_offload_dir", ""),
        )
    )
    _root_mode = str(getattr(runtime, "root_mode", getattr(runtime, "_root_mode", "assistant")))

    summ_config = SummarizationConfig(
        offload_dir=_offload_dir,
        thread_id=_tid,
    )

    _summarize_fn = (
        getattr(runtime.context_manager, "config", None)
        and runtime.context_manager.config.summarize_callback
    )

    session_scheduler = SessionMemoryScheduler(
        summarize_fn=_summarize_fn,
        config=SessionMemoryConfig(
            storage_dir=_offload_dir,
            thread_id=str(_tid),
        ),
        workspace_view=getattr(runtime, "workspace_view", None),
    )

    live_runtime_overlay: dict[str, Any] = {}
    session_compaction_callback = None
    runtime_view_provider = None

    def _remember_compaction(payload: dict[str, Any]) -> None:
        summary = str(payload.get("summary", "")).strip()
        if not summary:
            return
        metadata = (
            dict(payload.get("metadata", {}))
            if isinstance(payload.get("metadata", {}), dict)
            else {}
        )
        live_runtime_overlay["system_context"] = {
            "thread_id": _tid,
            "primary_mode": _root_mode,
            "working_summary": summary,
            "latest_compaction_boundary": dict(payload),
            "prompt_injection": "",
        }
        live_runtime_overlay["session"] = {
            "working_summary": summary,
            "compaction_summary": summary,
        }
        live_runtime_overlay["context_hygiene"] = {
            "summary_active": True,
            "current_cutoff_index": int(metadata.get("cutoff_index", 0) or 0),
            "last_microcompact_count": int(metadata.get("microcompact_count", 0) or 0),
            "history_snip_count": int(
                metadata.get("history_snip_count", payload.get("history_snip_count", 1)) or 0
            ),
            "latest_boundary": dict(payload),
        }
        task_runtime = getattr(runtime, "task_runtime", None)
        if task_runtime is not None and hasattr(task_runtime, "record_compaction_boundary"):
            try:
                task_runtime.record_compaction_boundary(
                    dict(live_runtime_overlay["system_context"].get("latest_compaction_boundary", {}))
                )
            except Exception:
                pass

    def _build_live_view() -> dict[str, Any] | None:
        system_overlay = (
            dict(live_runtime_overlay.get("system_context", {}))
            if isinstance(live_runtime_overlay.get("system_context"), dict)
            else {}
        )
        session_overlay = (
            dict(live_runtime_overlay.get("session", {}))
            if isinstance(live_runtime_overlay.get("session"), dict)
            else {}
        )
        context_hygiene_overlay = (
            dict(live_runtime_overlay.get("context_hygiene", {}))
            if isinstance(live_runtime_overlay.get("context_hygiene"), dict)
            else {}
        )
        latest_boundary = (
            dict(system_overlay.get("latest_compaction_boundary", {}))
            if isinstance(system_overlay.get("latest_compaction_boundary"), dict)
            else {}
        )

        def _compose_live_view(
            *,
            route_section: dict[str, Any] | None = None,
            hooks_section: dict[str, Any] | None = None,
        ):
            return build_projected_runtime_view(
                thread_id=_tid,
                root_mode=_root_mode,
                system_context={
                    "thread_id": _tid,
                    "primary_mode": _root_mode,
                    "working_summary": str(system_overlay.get("working_summary", "")).strip(),
                    "latest_compaction_boundary": latest_boundary,
                    "prompt_injection": str(system_overlay.get("prompt_injection", "")).strip(),
                },
                session={
                    "session_notebook_summary": session_scheduler.get_notes() or "",
                    "working_summary": str(session_overlay.get("working_summary", "")).strip(),
                    "compaction_summary": str(session_overlay.get("compaction_summary", "")).strip(),
                },
                workspace=projection,
                tasks=build_runtime_task_section(
                    task_runtime=task_runtime_projection,
                    recent_tool_runs=recent_tool_runs,
                    permission=permission_projection,
                    latest_compaction_boundary=latest_boundary,
                ),
                permission=permission_projection,
                settings=settings_projection,
                capability=capability_projection,
                context_hygiene=context_hygiene_overlay,
                hooks=hooks_section or hooks_projection,
                route=route_section or {},
                isolation=isolation_projection,
                team_memory=team_memory_projection,
            )

        workspace_view = getattr(runtime, "workspace_view", None)
        projection = {}
        if workspace_view is not None and hasattr(workspace_view, "build_projection"):
            try:
                projection = workspace_view.build_projection(limit=8)
            except Exception:
                projection = {}
        capability_projection: dict[str, Any] = {}
        settings_projection: dict[str, Any] = {}
        capability_bus = getattr(runtime, "capability_bus", None)
        if capability_bus is not None and hasattr(capability_bus, "get_tree_projection"):
            try:
                capability_projection = build_capability_tree_resume_projection(capability_bus.get_tree_projection())
            except Exception:
                capability_projection = {}
        trusted_settings = getattr(runtime, "trusted_settings", None)
        if trusted_settings is not None and hasattr(trusted_settings, "build_projection"):
            try:
                settings_projection = trusted_settings.build_projection()
            except Exception:
                settings_projection = {}
        hooks_projection: dict[str, Any] = {}
        hooks_runtime = getattr(runtime, "hooks_runtime", None)
        if hooks_runtime is not None and hasattr(hooks_runtime, "build_projection"):
            try:
                hooks_projection = hooks_runtime.build_projection()
            except Exception:
                hooks_projection = {}
        session_key = (
            runtime.session_runtime.session_key_for_thread(_tid)
            if getattr(runtime, "session_runtime", None) is not None
            else ""
        )
        isolation_projection = build_root_isolation_projection(
            workspace_dir=(
                getattr(getattr(runtime, "workspace", None), "root_dir", "")
                or getattr(getattr(runtime, "workspace", None), "_root_dir", "")
                or "."
            ),
            root_mode=_root_mode,
            multi_agent_ready=bool(
                getattr(runtime, "subagent_registry", None) is not None
                and getattr(runtime, "task_runtime", None) is not None
                and getattr(runtime, "middleware", None) is not None
            ),
            thread_id=_tid,
            session_key=session_key,
            hooks_runtime=hooks_runtime,
        )
        recent_tool_runs: list[dict[str, Any]] = []
        tool_middleware = getattr(runtime, "middleware", None)
        permission_projection: dict[str, Any] = {}
        if tool_middleware is not None and hasattr(tool_middleware, "get_control_snapshot"):
            try:
                snapshot = tool_middleware.get_control_snapshot()
                observability = snapshot.get("observability", {}) if isinstance(snapshot, dict) else {}
                recent_events = observability.get("recent_events", []) if isinstance(observability, dict) else []
                permission_projection = snapshot.get("permission", {}) if isinstance(snapshot, dict) else {}
                snapshot_settings = snapshot.get("settings", {}) if isinstance(snapshot, dict) else {}
                if isinstance(snapshot_settings, dict) and snapshot_settings:
                    settings_projection = snapshot_settings
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
                    recent_tool_runs.append(
                        {
                            "title": tool_name,
                            "status": status,
                            "source": "tool_control",
                            "run_id": str(event.get("tool_call_id", "")).strip(),
                            "preview": str(event.get("args_preview", "")).strip(),
                            "timestamp": event.get("timestamp"),
                        }
                    )
            except Exception:
                recent_tool_runs = []
        team_memory_projection: dict[str, Any] = {}
        subagent_registry = getattr(runtime, "subagent_registry", None)
        if subagent_registry is not None and hasattr(subagent_registry, "build_team_memory_projection"):
            try:
                team_memory_projection = subagent_registry.build_team_memory_projection(
                    team_key=session_key or _tid,
                    owner_session_key=session_key,
                    owner_thread_id=_tid,
                )
            except Exception:
                team_memory_projection = {}
        task_runtime_projection: dict[str, Any] = {}
        task_runtime = getattr(runtime, "task_runtime", None)
        if task_runtime is not None:
            try:
                if recent_tool_runs:
                    task_runtime.ingest_tool_runs(recent_tool_runs, source="tool_control")
                if isinstance(permission_projection, dict):
                    task_runtime.ingest_permission_events(
                        list(permission_projection.get("recent_events", [])),
                        source="permission_projection",
                    )
                if latest_boundary:
                    task_runtime.record_compaction_boundary(dict(latest_boundary))
                task_runtime_projection = task_runtime.build_projection() or {}
            except Exception:
                task_runtime_projection = {}
        preliminary_route: dict[str, Any] = {}
        preliminary_view = _compose_live_view().to_payload()
        if hooks_runtime is not None and hasattr(hooks_runtime, "run_phase"):
            try:
                preliminary_route = hooks_runtime.run_phase(
                    HookPhase.ROUTE_SELECTION,
                    {
                        "query": "",
                        "provides": "",
                        "projected_runtime_view": preliminary_view,
                    },
                )
            except Exception:
                preliminary_route = {}
        route_projection: dict[str, Any] = dict(preliminary_route)
        if capability_bus is not None and hasattr(capability_bus, "get_route_projection"):
            try:
                bus_route = capability_bus.get_route_projection(
                    query="",
                    provides="",
                    projected_runtime_view=preliminary_view,
                )
                route_projection = {
                    **dict(bus_route or {}),
                    "prefer_slots": list(preliminary_route.get("prefer_slots", [])),
                    "avoid_slots": list(preliminary_route.get("avoid_slots", [])),
                    "avoid_top_levels": list(preliminary_route.get("avoid_top_levels", [])),
                    "force_trunk_first": bool(preliminary_route.get("force_trunk_first")),
                    "notes": list(preliminary_route.get("notes", [])),
                }
                if isinstance(bus_route, dict):
                    recommended = dict(bus_route.get("recommended", {}))
                    summary = str(recommended.get("summary", "")).strip()
                    route_projection["summary"] = summary
            except Exception:
                route_projection = dict(preliminary_route)
        if hooks_runtime is not None and hasattr(hooks_runtime, "run_phase"):
            try:
                final_view = _compose_live_view(route_section=route_projection).to_payload()
                bookkeeping_hook = hooks_runtime.run_phase(
                    HookPhase.SESSION_BOOKKEEPING,
                    {
                        "projected_runtime_view": final_view,
                    },
                )
                if bookkeeping_hook.get("notes") or bookkeeping_hook.get("session_tags"):
                    hooks_projection = {
                        **hooks_projection,
                        "notes": list(bookkeeping_hook.get("notes", [])),
                        "session_tags": list(bookkeeping_hook.get("session_tags", [])),
                    }
            except Exception:
                pass
        final_live_view = _compose_live_view(route_section=route_projection, hooks_section=hooks_projection)
        artifacts = compile_runtime_resume_view(final_live_view)
        if capability_bus is not None and hasattr(capability_bus, "share_context") and artifacts is not None:
            try:
                capability_bus.share_context(
                    "projected_runtime_view",
                    final_live_view.to_payload(),
                    source="runtime.artifacts",
                )
            except Exception:
                pass
        if getattr(runtime, "session_runtime", None) is not None and artifacts is not None:
            try:
                runtime.session_runtime.update_runtime_view(
                    thread_id=_tid,
                    session_key=runtime.session_runtime.session_key_for_thread(_tid),
                    root_mode=_root_mode,
                    source="runtime.artifacts",
                    projected_runtime_view=final_live_view.to_payload(),
                )
            except Exception:
                pass
        return artifacts
    if getattr(runtime, "session_runtime", None) is not None:
        _sr = runtime.session_runtime

        def _record_session_compaction(payload: dict[str, Any]) -> None:
            _remember_compaction(payload)
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
                root_mode=_root_mode,
            )

        session_compaction_callback = _record_session_compaction

        def _get_artifacts() -> dict[str, Any] | None:
            sk = _sr.session_key_for_thread(_tid)
            base = _sr.get_compiled_runtime_view(sk) if sk else None
            return merge_session_runtime_view(base, _build_live_view())

        runtime_view_provider = _get_artifacts
    else:
        session_compaction_callback = _remember_compaction
        runtime_view_provider = _build_live_view

    if getattr(runtime, "middleware", None) is not None and hasattr(runtime.middleware, "set_runtime_view_provider"):
        try:
            runtime.middleware.set_runtime_view_provider(
                lambda: (
                    dict((runtime_view_provider() or {}).get("projected_runtime_view", {}))
                    if runtime_view_provider is not None
                    else None
                )
            )
        except Exception:
            pass

    kwargs: dict[str, Any] = dict(
        model=runtime.llm,
        tools=assembly.all_tools,
        system_prompt=assembly.system_prompt,
        middleware=build_root_langchain_middleware(
            runtime=runtime,
            summarize_fn=_summarize_fn,
            summarization_config=summ_config,
            session_compaction_callback=session_compaction_callback,
            session_memory_extractor=session_scheduler,
            runtime_view_provider=runtime_view_provider,
        ),
        checkpointer=runtime.checkpointer,
    )
    if response_format is not None:
        kwargs["response_format"] = response_format

    from core.systems.governance.approval_callback import GovernanceApprovalCallback
    import sys as _sys

    _HIGH_RISK_TOOL_NAMES = {"write_file", "str_replace", "bash"}
    high_risk_tools = list(
        _HIGH_RISK_TOOL_NAMES
        | {t.name for t in assembly.all_tools if getattr(t, "risk_level", "low") in ("high", "critical")}
    )

    _ask_user_fn = None
    if _sys.stdin.isatty():
        def _cli_ask_user_fn(tool_name: str, input_str: str) -> bool:
            """Interactive CLI confirmation for high-risk tool calls."""
            try:
                answer = input(
                    f"\n[治理中心] ⚠️  高危工具 '{tool_name}' 即将执行。\n"
                    f"参数: {input_str}\n"
                    "是否允许执行？[y/N] "
                ).strip().lower()
                return answer in ("y", "yes")
            except (EOFError, KeyboardInterrupt):
                return False
        _ask_user_fn = _cli_ask_user_fn

    governance_callback = GovernanceApprovalCallback(
        high_risk_tools=high_risk_tools,
        approval_queue=runtime.approval_queue,
        thread_id=_tid,
        ask_user_fn=_ask_user_fn,
    )
    runtime._governance_callback = governance_callback

    runtime.middleware.set_ask_user_fn(_ask_user_fn)

    kwargs["model"] = runtime.llm.with_config({"callbacks": [governance_callback]})

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
