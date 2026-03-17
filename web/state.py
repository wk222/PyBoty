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

from agent import create_tool_creator_agent
from core.app_manager import AppManager
from core.approval_orchestrator import ApprovalOrchestrator
from core.approval_queue import ApprovalQueue
from core.config import get_agent_control_config, get_llm_config
from core.memory_manager import MemoryManager
from core.project_paths import ProjectPaths
from core.skill_registry import SkillRegistry
from core.task_scheduler import TaskScheduler
from core.uv_env_manager import UvEnvManager
from core.workspace_manager import WorkspaceManager

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
    ):
        self.paths = paths
        self.llm_config = llm_config
        self.control_config = control_config
        self.approval_queue = approval_queue
        self._lock = threading.Lock()
        self._agents: dict[str, Any] = {}

    def get(self, thread_id: str) -> Any | None:
        return self._agents.get(thread_id)

    def get_or_create(self, thread_id: str) -> Any:
        agent = self._agents.get(thread_id)
        if agent is not None:
            return agent

        with self._lock:
            agent = self._agents.get(thread_id)
            if agent is None:
                agent = create_tool_creator_agent(
                    model=self.llm_config.get("model", "gpt-4"),
                    thread_id=thread_id,
                    api_key=self.llm_config.get("api_key"),
                    base_url=self.llm_config.get("api_base"),
                    paths=self.paths,
                    control_config=self.control_config,
                    approval_queue=self.approval_queue,
                )
                self._agents[thread_id] = agent
            return agent

    @property
    def has_api_key(self) -> bool:
        """Check if an LLM API key is configured."""
        key = self.llm_config.get("api_key") or ""
        if key:
            return True
        import os

        return bool(os.environ.get("OPENAI_API_KEY"))

    def remove(self, thread_id: str) -> None:
        with self._lock:
            self._agents.pop(thread_id, None)


@dataclass
class WebServices:
    """Application-scoped services shared by routers and startup hooks."""

    paths: ProjectPaths
    llm_config: dict[str, Any]
    control_config: dict[str, Any]
    approval_queue: ApprovalQueue
    approvals: ApprovalOrchestrator
    conversations: ConversationStore
    agents: AgentPool
    workspace_mgr: WorkspaceManager
    memory_mgr: MemoryManager
    skill_registry: SkillRegistry
    task_scheduler: TaskScheduler
    uv_env_mgr: UvEnvManager
    app_manager: AppManager

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
            agents=AgentPool(resolved_paths, config, resolved_control, approval_queue),
            workspace_mgr=WorkspaceManager(str(resolved_paths.workspace_dir)),
            memory_mgr=MemoryManager(str(resolved_paths.workspace_dir)),
            skill_registry=SkillRegistry(str(resolved_paths.skills_dir)),
            task_scheduler=TaskScheduler(str(resolved_paths.workspace_dir)),
            uv_env_mgr=UvEnvManager(str(resolved_paths.uv_envs_dir)),
            app_manager=AppManager(str(resolved_paths.apps_dir), project_paths=resolved_paths),
        )
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
        self.task_scheduler.start()

    def shutdown(self) -> None:
        self.task_scheduler.stop()
