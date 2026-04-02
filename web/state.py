"""Shared runtime state for the PyBot web service."""

from __future__ import annotations

import json
import logging
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from agent import create_admin_agent, create_app_matrix_agent, create_tool_creator_agent
from core.assets.apps.manager import AppManager
from core.assets.apps.orchestration import AppOrchestrationRegistry
from core.assets.apps.runtime import AppMatrixRuntime
from core.assets.skills import SkillMarketplace, SkillRegistry
from core.assets.workflows.scheduling import TaskQueue, TaskScheduler
from core.modes import resolve_mode_profile
from core.systems.bus import CapabilityBus, CapabilityRegistry
from core.systems.governance import ApprovalOrchestrator, ApprovalQueue
from core.systems.integration import GatewayRuntime
from core.systems.memory import MemoryManager
from core.systems.runtime import (
    ProjectPaths,
    SessionRuntime,
    UvEnvManager,
    WorkspaceManager,
    get_agent_control_config,
    get_extra_skill_sources,
    get_llm_config,
    reload_config,
)
from core.systems.runtime.event_bus import event_bus

logger = logging.getLogger(__name__)


class ConversationStore:
    """Thread-safe conversation metadata and history persistence."""

    def __init__(self, paths: ProjectPaths):
        self.paths = paths
        self._lock = threading.RLock()
        self.paths.ensure_runtime_dirs()
        self._conversations = self._load_conversations_unlocked()

    def _load_json(self, path: Path, default: Any) -> Any:
        if not path.exists():
            return default
        try:
            with path.open("r", encoding="utf-8") as file:
                return json.load(file)
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning("Failed to load JSON store %s: %s", path, exc)
            return default

    def _dump_json(self, path: Path, payload: Any) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temp_path = path.with_name(f"{path.name}.tmp")
        with temp_path.open("w", encoding="utf-8") as file:
            json.dump(payload, file, ensure_ascii=False, indent=2)
        temp_path.replace(path)

    def _load_conversations_unlocked(self) -> dict[str, dict[str, Any]]:
        data = self._load_json(self.paths.conversations_file, {})
        return data if isinstance(data, dict) else {}

    def _save_conversations_unlocked(self) -> None:
        self._dump_json(self.paths.conversations_file, self._conversations)

    def _history_path(self, thread_id: str) -> Path:
        return self.paths.chat_history_dir / f"{thread_id}.json"

    def list_conversations(self) -> list[dict[str, Any]]:
        with self._lock:
            items = [{"thread_id": thread_id, **meta} for thread_id, meta in self._conversations.items()]
        items.sort(key=lambda item: item.get("last_message_at", 0), reverse=True)
        return items

    def create_conversation(self, title: str | None = None) -> dict[str, Any]:
        with self._lock:
            thread_id = f"session-{uuid.uuid4().hex[:8]}"
            now = time.time()
            metadata = {
                "title": title or f"新会话 {len(self._conversations) + 1}",
                "created_at": now,
                "last_message_at": now,
                "message_count": 0,
            }
            self._conversations[thread_id] = metadata
            self._save_conversations_unlocked()
        return {"thread_id": thread_id, "title": metadata["title"]}

    def ensure_conversation(self, thread_id: str, title_hint: str | None = None) -> None:
        with self._lock:
            if thread_id in self._conversations:
                return
            now = time.time()
            title = title_hint or f"新会话 {len(self._conversations) + 1}"
            self._conversations[thread_id] = {
                "title": title[:30] + ("..." if len(title) > 30 else ""),
                "created_at": now,
                "last_message_at": now,
                "message_count": 0,
            }
            self._save_conversations_unlocked()

    def delete_conversation(self, thread_id: str) -> None:
        history_path = self._history_path(thread_id)
        with self._lock:
            self._conversations.pop(thread_id, None)
            self._save_conversations_unlocked()
            if history_path.exists():
                history_path.unlink()

    def get_history(self, thread_id: str) -> list[dict[str, Any]]:
        with self._lock:
            history = self._load_json(self._history_path(thread_id), [])
        return history if isinstance(history, list) else []

    def append_message(self, thread_id: str, role: str, content: str, *, title_hint: str | None = None) -> None:
        with self._lock:
            self.ensure_conversation(thread_id, title_hint=title_hint)
            history_path = self._history_path(thread_id)
            history = self._load_json(history_path, [])
            history.append({"role": role, "content": content, "timestamp": time.time()})
            self._dump_json(history_path, history)
            self._conversations[thread_id]["last_message_at"] = time.time()
            self._conversations[thread_id]["message_count"] = len(history)
            self._save_conversations_unlocked()


class AgentPool:
    """Lazily created service agents keyed by thread id."""

    def __init__(
        self,
        paths: ProjectPaths,
        llm_config: dict[str, Any],
        control_config: dict[str, Any],
        approval_queue: ApprovalQueue,
        session_runtime: SessionRuntime | None = None,
    ):
        self.paths = paths
        self.llm_config = llm_config
        self.control_config = control_config
        self.approval_queue = approval_queue
        self.session_runtime = session_runtime
        self._lock = threading.Lock()
        self._agents: dict[tuple[str, str], Any] = {}

    @staticmethod
    def _normalize_mode(root_mode: str | None) -> str:
        return resolve_mode_profile(root_mode or "assistant").name

    def _agent_key(self, thread_id: str, *, root_mode: str = "assistant") -> tuple[str, str]:
        return (self._normalize_mode(root_mode), thread_id)

    def get(self, thread_id: str, *, root_mode: str = "assistant") -> Any | None:
        return self._agents.get(self._agent_key(thread_id, root_mode=root_mode))

    def _fresh_runtime_config(self) -> dict[str, Any]:
        """Re-read config.json so hot-updated API keys are picked up."""
        try:
            return reload_config()
        except Exception:
            return {"llm_config": self.llm_config}

    def _fresh_llm_config(self) -> dict[str, Any]:
        runtime_cfg = self._fresh_runtime_config()
        llm_cfg = runtime_cfg.get("llm_config", self.llm_config)
        return llm_cfg if isinstance(llm_cfg, dict) else self.llm_config

    def _fresh_llm_fallback_config(self) -> list[dict[str, Any]]:
        runtime_cfg = self._fresh_runtime_config()
        fallback = runtime_cfg.get("llm_fallback", [])
        return fallback if isinstance(fallback, list) else []

    def get_or_create(self, thread_id: str) -> Any:
        return self.get_or_create_mode("assistant", thread_id)

    @property
    def storage(self) -> Any:
        """Provide fallback access to global tool storage via the default agent."""
        return self.get_or_create_mode("assistant", "default").storage

    def get_or_create_mode(self, root_mode: str, thread_id: str) -> Any:
        normalized_mode = self._normalize_mode(root_mode)
        key = self._agent_key(thread_id, root_mode=normalized_mode)
        agent = self._agents.get(key)
        if agent is not None:
            return agent

        with self._lock:
            agent = self._agents.get(key)
            if agent is None:
                cfg = self._fresh_llm_config()
                fallback_cfg = self._fresh_llm_fallback_config()
                factory = {
                    "assistant": create_tool_creator_agent,
                    "app_matrix": create_app_matrix_agent,
                    "admin": create_admin_agent,
                }.get(normalized_mode, create_tool_creator_agent)
                agent = factory(
                    model=cfg.get("model", "gpt-4"),
                    thread_id=thread_id,
                    api_key=cfg.get("api_key"),
                    base_url=cfg.get("api_base"),
                    provider=cfg.get("provider"),
                    fallback_configs=fallback_cfg,
                    paths=self.paths,
                    control_config=self.control_config,
                    approval_queue=self.approval_queue,
                    session_runtime=self.session_runtime,
                )
                self._agents[key] = agent
            return agent

    @property
    def has_api_key(self) -> bool:
        """Check if an LLM API key is configured."""
        cfg = self._fresh_llm_config()
        key = cfg.get("api_key") or ""
        if key:
            return True
        import os

        return bool(os.environ.get("OPENAI_API_KEY"))

    def remove(self, thread_id: str) -> None:
        self.remove_mode("assistant", thread_id)

    def remove_mode(self, root_mode: str, thread_id: str) -> None:
        with self._lock:
            self._agents.pop(self._agent_key(thread_id, root_mode=root_mode), None)


@dataclass
class WebServices:
    """Application-scoped services shared by routers and startup hooks."""

    paths: ProjectPaths
    llm_config: dict[str, Any]
    control_config: dict[str, Any]
    approval_queue: ApprovalQueue
    approvals: ApprovalOrchestrator
    conversations: ConversationStore
    session_runtime: SessionRuntime
    agents: AgentPool
    workspace_mgr: WorkspaceManager
    memory_mgr: MemoryManager
    capability_bus: CapabilityBus
    capability_registry: CapabilityRegistry
    skill_marketplace: SkillMarketplace
    skill_registry: SkillRegistry
    task_scheduler: TaskScheduler
    task_queue: TaskQueue
    uv_env_mgr: UvEnvManager
    app_manager: AppManager
    gateway_runtime: GatewayRuntime

    @staticmethod
    def _build_skill_registry(resolved_paths: ProjectPaths) -> SkillRegistry:
        from core.assets.skills.skill_sources import SkillSource

        extra_cfg = get_extra_skill_sources()
        if not extra_cfg:
            return SkillRegistry(str(resolved_paths.skills_dir))
        sources: list[str | SkillSource] = [
            SkillSource(
                name=e.get("name", f"external_{i}"),
                path=e["path"],
                writable=False,
                flavor=e.get("flavor", "generic"),
            )
            for i, e in enumerate(extra_cfg)
        ]
        sources.append(SkillSource(name="workspace", path=str(resolved_paths.skills_dir), writable=True))
        return SkillRegistry(None, skill_sources=sources)

    @classmethod
    def create(
        cls,
        *,
        paths: ProjectPaths | None = None,
        llm_config: dict[str, Any] | None = None,
        control_config: dict[str, Any] | None = None,
    ) -> WebServices:
        resolved_paths = paths or ProjectPaths.from_root()
        resolved_paths.ensure_runtime_dirs()
        config = llm_config or get_llm_config()
        resolved_control = control_config or get_agent_control_config()
        approval_queue = ApprovalQueue(storage_path=resolved_paths.approvals_file)
        session_runtime = SessionRuntime(resolved_paths.sessions_file, event_bus=event_bus)
        services = cls(
            paths=resolved_paths,
            llm_config=config,
            control_config=resolved_control,
            approval_queue=approval_queue,
            approvals=ApprovalOrchestrator(
                approval_queue=approval_queue,
                get_agent_for_thread=lambda thread_id: None,
                get_system_agent=lambda: None,
            ),
            conversations=ConversationStore(resolved_paths),
            session_runtime=session_runtime,
            agents=AgentPool(
                resolved_paths,
                config,
                resolved_control,
                approval_queue,
                session_runtime=session_runtime,
            ),
            workspace_mgr=WorkspaceManager(str(resolved_paths.workspace_dir)),
            memory_mgr=MemoryManager(str(resolved_paths.workspace_dir)),
            capability_bus=CapabilityBus(str(resolved_paths.workspace_dir)),
            capability_registry=None,  # type: ignore[arg-type]
            skill_marketplace=SkillMarketplace(str(resolved_paths.workspace_dir)),
            skill_registry=cls._build_skill_registry(resolved_paths),
            task_scheduler=TaskScheduler(str(resolved_paths.workspace_dir)),
            task_queue=TaskQueue(max_workers=4),
            uv_env_mgr=UvEnvManager(str(resolved_paths.uv_envs_dir)),
            app_manager=AppManager(str(resolved_paths.apps_dir), project_paths=resolved_paths),
            gateway_runtime=GatewayRuntime(resolved_paths.workspace_data_dir),
        )
        services.capability_registry = CapabilityRegistry(
            workspace_dir=resolved_paths.workspace_dir,
            capability_bus=services.capability_bus,
            skill_marketplace=services.skill_marketplace,
            skill_registry=services.skill_registry,
            app_manager=services.app_manager,
        )
        services.session_runtime.sync_conversations(services.conversations)
        services.session_runtime.sync_gateway_runtime(services.gateway_runtime)
        services.approvals = ApprovalOrchestrator(
            approval_queue=approval_queue,
            get_agent_for_thread=lambda thread_id, services=services: services.agents.get_or_create(thread_id),
            get_system_agent=lambda services=services: services.system_agent(),
        )
        return services

    @property
    def llm_configured(self) -> bool:
        """Return True if an LLM API key is available."""
        return self.agents.has_api_key

    def system_agent(self) -> Any:
        return self.agents.get_or_create("__system__")

    def app_matrix_runtime(self) -> AppMatrixRuntime:
        registry = AppOrchestrationRegistry(storage_path=self.paths.workspace_data_dir / "app_orchestration.json")
        return AppMatrixRuntime(
            app_manager=self.app_manager,
            orchestration_registry=registry,
            capability_registry=self.capability_registry,
        )

    def sync_session_spine(self) -> None:
        self.session_runtime.sync_conversations(self.conversations)
        self.session_runtime.sync_gateway_runtime(self.gateway_runtime)
        try:
            self.session_runtime.sync_workflow_runtime(self.system_agent().pyflow_engine)
        except Exception:
            logger.debug("Failed to sync workflow runtime into session spine", exc_info=True)

    def ensure_workflow_session(
        self,
        *,
        workflow: Any,
        source: str,
        thread_id: str = "",
        session_key: str = "",
        root_mode: str = "assistant",
        title: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        provided_thread_id = str(thread_id).strip() or str(workflow.variables.get("thread_id", "")).strip()
        provided_session_key = str(session_key).strip() or str(workflow.variables.get("session_key", "")).strip()
        resolved_root_mode = (
            str(root_mode).strip()
            or str(workflow.variables.get("root_mode", workflow.variables.get("mode", "assistant"))).strip()
            or "assistant"
        )
        if not provided_thread_id:
            base_name = str(getattr(workflow, "name", "") or getattr(workflow, "id", "") or "workflow").strip()
            slug = base_name.replace(" ", "-").replace("/", "-").replace("\\", "-")[:48] or "workflow"
            provided_thread_id = f"workflow-{slug}-{uuid.uuid4().hex[:8]}"
        if provided_session_key:
            session = self.session_runtime.ensure_session(
                session_key=provided_session_key,
                thread_id=provided_thread_id,
                root_mode=resolved_root_mode,
                source=source,
                title=title or str(getattr(workflow, "name", "")).strip(),
                metadata=metadata,
            )
        else:
            session = self.session_runtime.ensure_thread_session(
                provided_thread_id,
                root_mode=resolved_root_mode,
                source=source,
                title=title or str(getattr(workflow, "name", "")).strip(),
                metadata=metadata,
            )
        workflow.variables["thread_id"] = session["thread_id"]
        workflow.variables["session_key"] = session["session_key"]
        workflow.variables["root_mode"] = session["primary_mode"]
        workflow.variables["source"] = source
        return session

    def startup(self) -> None:
        self.approvals = ApprovalOrchestrator(
            approval_queue=self.approval_queue,
            get_agent_for_thread=lambda thread_id: self.agents.get_or_create(thread_id),
            get_system_agent=lambda: self.system_agent(),
        )

        def schedule_callback(prompt: str, task_id: str) -> None:
            agent = self.agents.get_or_create(task_id)
            agent.chat(prompt)

        self.task_scheduler.set_agent_callback(schedule_callback)
        self.capability_registry.refresh_local_index(save=True)
        self.sync_session_spine()
        self.task_scheduler.start()

    def shutdown(self) -> None:
        self.task_scheduler.stop()
        self.task_queue.shutdown(wait=False)
