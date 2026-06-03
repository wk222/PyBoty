"""Bootstrap helpers for assembling the root PyBot runtime."""

from __future__ import annotations

import sqlite3
import time as time_module
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    from langchain_core.language_models import BaseChatModel
    from core.systems.runtime.backend_protocol import (
        AgentStorageProtocol,
        AppManagerProtocol,
        SkillRegistryProtocol,
        ToolStorageProtocol,
        TaskSchedulerProtocol,
        SubagentRegistryProtocol,
    )

from langgraph.checkpoint.sqlite import SqliteSaver

from core.systems.capability import CapabilityBus, CapabilityRegistry
from core.systems.context import ContextWindowManager, WorkspaceViewService
from core.systems.governance import AgentControlPolicy, ApprovalQueue
from core.systems.integration import ChannelManager, MCPHub
from core.systems.memory import MemoryEngine, build_memory_engine
from core.systems.middleware.middleware_stack import MiddlewareStack
from core.systems.runtime.backend_protocol import LocalSandboxBackend
from core.systems.runtime.config_impl import (
    get_channel_routes_config,
    get_channels_config,
    get_extra_skill_sources,
    get_rag_config,
    get_trusted_settings,
)
from core.systems.llm import (
    ModelProviderError,
    create_failover_model,
    resolve_model,
)
from core.systems.runtime.daemon import BackgroundDaemon
from core.systems.runtime.hooks_runtime import HookPhase, create_default_hooks_runtime
from core.systems.runtime.project_paths import ProjectPaths
from core.systems.runtime.runtime_capability_bundle import build_capability_runtime_bundle
from core.systems.runtime.task_runtime import TaskRuntimeService
from core.systems.runtime.workspace_manager import WorkspaceManager


@dataclass
class ToolAssembly:
    """Resolved tool set and prompt for the root agent."""

    creator_tools: list[Any]
    dynamic_tools: list[Any]
    all_tools: list[Any]
    system_prompt: str
    tool_groups: list[tuple[str, int]]


@dataclass
class PyBotRuntime:
    """Runtime services shared by the root PyBot instance."""

    thread_id: str
    conversation_offload_dir: str
    root_mode: str
    storage: Any  # ToolStorage
    agent_storage: Any  # AgentStorage
    backend: LocalSandboxBackend
    workspace: WorkspaceManager
    workspace_view: WorkspaceViewService
    memory: MemoryEngine
    skill_registry: Any  # SkillRegistry
    scheduler: Any  # TaskScheduler
    app_manager: Any  # AppManager
    skill_marketplace: Any  # SkillMarketplace
    mcp_hub: MCPHub
    channel_manager: ChannelManager
    pyflow_engine: Any  # PyFlowEngine
    tool_chain: Any  # ToolChainExecutor
    eval_framework: Any  # EvalFramework
    context_manager: ContextWindowManager
    capability_bus: CapabilityBus
    capability_registry: CapabilityRegistry
    middleware_stack: MiddlewareStack
    llm: BaseChatModel
    checkpointer: SqliteSaver
    middleware: Any  # DynamicToolMiddleware
    control_policy: AgentControlPolicy
    approval_queue: ApprovalQueue
    subagent_registry: Any  # SubagentRegistry
    swarm_scheduler: Any  # SwarmScheduler
    daemon: BackgroundDaemon
    task_runtime: TaskRuntimeService
    hooks_runtime: Any | None = None
    trusted_settings: Any | None = None
    session_runtime: Any | None = None
    orchestration_registry: Any | None = None
    knowledge_tools: list[Any] = field(default_factory=list)


def create_llm_client(
    *,
    model: str,
    temperature: float,
    api_key: str | None = None,
    base_url: str | None = None,
    provider: str | None = None,
    fallback_configs: list[dict[str, Any]] | None = None,
) -> BaseChatModel:
    """Create an LLM client via multi-provider resolver with optional failover."""
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
        from langchain_openai import ChatOpenAI
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
    from core.assets.tools.tool_storage import ToolStorage
    from core.assets.agents.storage import AgentStorage
    from core.systems.apps.app_manager import AppManager
    from core.assets.skills.skill_marketplace import SkillMarketplace
    from core.assets.skills.skill_registry import SkillRegistry
    from core.assets.workflows.scheduling import TaskScheduler
    from core.assets.workflows.pyflow_engine import PyFlowEngine
    from core.assets.tools.tool_chain import ToolChainExecutor
    from core.assets.tools import DynamicToolMiddleware
    from core.systems.runtime.daemon import SessionReaper
    from core.systems.apps.app_manager_registry import set_shared_app_manager
    from core.assets.workflows import workflow_orchestration
    from core.systems.governance.permission_policy import PermissionControlPlane

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
    from core.systems.apps import app_runtime
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
    
    subagent_registry = capability_bundle.subagent_registry
    from core.systems.agents.swarm_scheduler import SwarmScheduler
    swarm_scheduler = SwarmScheduler(registry=subagent_registry)

    knowledge_tools_list: list[Any] = []
    vector_store_for_memory: Any | None = None
    try:
        rag_cfg = get_rag_config(config=effective_config)
        if rag_cfg.get("enabled"):
            from core.systems.knowledge.document_pipeline import ChunkConfig, DocumentPipeline
            from core.systems.knowledge.embedding_resolver import resolve_embeddings
            from core.systems.knowledge.knowledge_tools import get_knowledge_tools
            from core.systems.knowledge.vector_store import create_vector_store

            persist_dir = rag_cfg.get("persist_dir") or str(paths.workspace_dir / "vector_store")
            embedding_spec = rag_cfg.get("embedding_model")
            emb_fn = resolve_embeddings(embedding_spec, api_key=api_key)
            vector_store_for_memory = create_vector_store(
                backend=rag_cfg.get("backend", "chroma"),
                persist_dir=persist_dir,
                embedding_function=emb_fn,
            )
            pipeline = DocumentPipeline(
                vector_store_for_memory,
                chunk_config=ChunkConfig(chunk_size=1000, chunk_overlap=200),
            )
            knowledge_tools_list = get_knowledge_tools(vector_store_for_memory, pipeline)
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

    embeddings = getattr(vector_store_for_memory, "_embedding_function", None)
    memory_engine = build_memory_engine(
        paths.workspace_dir,
        embeddings=embeddings,
        llm=llm,
    )

    conn = sqlite3.connect(str(paths.checkpoints_db), check_same_thread=False)
    checkpointer = SqliteSaver(conn)
    
    middleware = DynamicToolMiddleware(
        tool_storage=storage,
        control_policy=capability_bundle.control_policy,
        approval_queue=capability_bundle.approval_queue,
        approval_scope=f"root:{thread_id}",
        allowed_path_roots=[str(paths.runtime_root_dir), str(paths.workspace_dir), str(paths.tools_workspace_dir)],
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
        lease_manager=capability_bundle.pyflow_engine.node_runtime.operator.lease_manager,
        task_queue=None,
    )
    daemon.add_job("session_reaper", 60.0, reaper.run_reap_cycle)

    from core.modes.admin.watcher import AdminWatcherDaemon
    AdminWatcherDaemon(llm=llm, daemon=daemon, workspace_dir=paths.workspace_dir, interval_sec=120.0)
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
        memory=memory_engine,
        skill_registry=skill_registry,
        scheduler=scheduler,
        app_manager=app_manager,
        skill_marketplace=skill_marketplace,
        mcp_hub=mcp_hub,
        channel_manager=channel_manager,
        pyflow_engine=capability_bundle.pyflow_engine,
        tool_chain=capability_bundle.tool_chain,
        eval_framework=capability_bundle.eval_framework,
        context_manager=capability_bundle.context_manager,
        capability_bus=capability_bundle.capability_bus,
        capability_registry=capability_bundle.capability_registry,
        middleware_stack=capability_bundle.middleware_stack,
        llm=llm,
        checkpointer=checkpointer,
        middleware=middleware,
        control_policy=capability_bundle.control_policy,
        approval_queue=capability_bundle.approval_queue,
        subagent_registry=subagent_registry,
        daemon=daemon,
        session_runtime=session_runtime,
        hooks_runtime=hooks_runtime,
        knowledge_tools=knowledge_tools_list,
        swarm_scheduler=swarm_scheduler,
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
    from core.modes.factories import assemble_primary_tools as _impl
    return _impl(
        runtime=runtime,
        paths=paths,
        enable_agent_creation=enable_agent_creation,
        root_mode=root_mode,
        llm_factory=llm_factory,
        chat_callback=chat_callback,
    )


def create_root_agent(
    *,
    runtime: PyBotRuntime,
    assembly: ToolAssembly,
    response_format: Any | None = None,
) -> Any:
    """Build the root LangChain agent instance."""
    from core.systems.middleware.summarization_middleware import SummarizationConfig
    from core.systems.session.session_notes import SessionMemoryConfig, SessionMemoryScheduler
    from core.systems.session.session_runtime_view import merge_session_runtime_view
    from core.systems.middleware.agent_middleware_factory import build_root_langchain_middleware
    from langchain.agents import create_agent
    
    _tid = str(runtime.thread_id)
    _offload_dir = str(runtime.conversation_offload_dir)
    _root_mode = str(runtime.root_mode)

    summ_config = SummarizationConfig(offload_dir=_offload_dir, thread_id=_tid)
    _summarize_fn = getattr(runtime.context_manager.config, "summarize_callback", None)

    session_scheduler = SessionMemoryScheduler(
        summarize_fn=_summarize_fn,
        config=SessionMemoryConfig(storage_dir=_offload_dir, thread_id=_tid),
        workspace_view=runtime.workspace_view,
    )
    if runtime.memory is not None and hasattr(runtime.memory, "attach_session_scheduler"):
        runtime.memory.attach_session_scheduler(session_scheduler)

    live_runtime_overlay: dict[str, Any] = {}

    def _remember_compaction(payload: dict[str, Any]) -> None:
        summary = str(payload.get("summary", "")).strip()
        if not summary: return
        metadata = dict(payload.get("metadata", {}))
        live_runtime_overlay["system_context"] = {
            "thread_id": _tid,
            "primary_mode": _root_mode,
            "working_summary": summary,
            "latest_compaction_boundary": dict(payload),
        }
        live_runtime_overlay["context_hygiene"] = {
            "summary_active": True,
            "history_snip_count": int(metadata.get("history_snip_count", 1)),
            "latest_boundary": dict(payload),
        }

    def _build_live_view() -> dict[str, Any] | None:
        from core.systems.context.projected_runtime_view import ProjectedRuntimeView
        if not hasattr(runtime, "session_memory_extractor") or runtime.session_memory_extractor is None:
            setattr(runtime, "session_memory_extractor", session_scheduler)

        view = ProjectedRuntimeView.from_runtime(
            runtime=runtime,
            system_overlay=dict(live_runtime_overlay.get("system_context", {})),
            session_overlay=dict(live_runtime_overlay.get("session", {})),
            context_hygiene_overlay=dict(live_runtime_overlay.get("context_hygiene", {})),
        )
        return view.to_resume_dict()

    session_compaction_callback = _remember_compaction
    runtime_view_provider = _build_live_view

    if runtime.session_runtime is not None:
        _sr = runtime.session_runtime
        def _record_session_compaction(payload: dict[str, Any]) -> None:
            _remember_compaction(payload)
            _sr.record_external_compaction(
                thread_id=_tid,
                session_key=_sr.session_key_for_thread(_tid),
                summary=str(payload.get("summary", "")),
                source=str(payload.get("source", "middleware.summarization")),
                root_mode=_root_mode,
            )
        session_compaction_callback = _record_session_compaction
        runtime_view_provider = lambda: merge_session_runtime_view(_sr.get_compiled_runtime_view(_sr.session_key_for_thread(_tid)), _build_live_view())

    if hasattr(runtime.middleware, "set_runtime_view_provider"):
        runtime.middleware.set_runtime_view_provider(
            lambda: dict((runtime_view_provider() or {}).get("projected_runtime_view", {}))
        )

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
    governance_callback = GovernanceApprovalCallback(
        high_risk_tools=["write_file", "str_replace", "bash"],
        approval_queue=runtime.approval_queue,
        thread_id=_tid,
    )
    kwargs["model"] = runtime.llm.with_config({"callbacks": [governance_callback]})

    return create_agent(**kwargs)


def invoke_sub_agent(
    *,
    agent_storage: AgentStorageProtocol,
    global_tool_storage: ToolStorageProtocol | None = None,
    llm_factory: Callable[[str | None, float | None], BaseChatModel],
    control_policy: Any | None = None,
    approval_queue: Any | None = None,
    project_paths: Any | None = None,
    subagent_registry: Any | None = None,
    agent_name: str,
    task: str,
    context: str = "",
    thread_id: str | None = None,
    swarm_scheduler: Any | None = None,
) -> dict[str, Any]:
    """Invoke a named sub-agent and return its structured response payload."""
    from core.systems.agents.agent_services import invoke_persisted_agent
    resolved_thread_id = thread_id or f"workflow_{agent_name}_{int(time_module.time())}"
    
    invoke_kwargs = {
        "agent_storage": agent_storage,
        "global_tool_storage": global_tool_storage,
        "llm_factory": llm_factory,
        "control_policy": control_policy,
        "approval_queue": approval_queue,
        "project_paths": project_paths,
        "subagent_registry": subagent_registry,
        "context": context,
        "thread_id": resolved_thread_id,
        "parent_agent_name": "root",
        "parent_depth": 0,
    }

    if swarm_scheduler is not None:
        run_id = swarm_scheduler.spawn_managed(
            agent_name=agent_name,
            task=task,
            invoke_fn=invoke_persisted_agent,
            invoke_kwargs=invoke_kwargs,
            parent_agent_name="root"
        )
        return {"status": "started", "run_id": run_id, "thread_id": resolved_thread_id}

    return invoke_persisted_agent(agent_name=agent_name, task=task, **invoke_kwargs)
