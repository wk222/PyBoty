"""Agent-to-Agent (A2A) communication protocol.

Enables multiple PyBot instances to discover each other and exchange
tasks, results, and capability advertisements over HTTP.

Protocol overview:
  1. Agent Card — Each instance publishes a JSON agent card describing
     its capabilities, skills, and contact endpoint.
  2. Task Exchange — One agent can send a task to another, receive
     progress updates, and collect the final result.
  3. Capability Discovery — Agents query each other's capabilities to
     find the best agent for a given task.

Wire format follows a simplified subset of Google's A2A specification,
using JSON-RPC style messages over HTTP POST.
"""

from __future__ import annotations

import json
import logging
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

logger = logging.getLogger(__name__)


class TaskState(str, Enum):
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass
class AgentCard:
    """Describes a PyBot instance's capabilities for discovery."""

    agent_id: str
    name: str
    description: str
    endpoint: str
    version: str = "1.0"
    capabilities: list[str] = field(default_factory=list)
    skills: list[str] = field(default_factory=list)
    protocols: list[str] = field(default_factory=lambda: ["a2a/1.0"])
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "agent_id": self.agent_id,
            "name": self.name,
            "description": self.description,
            "endpoint": self.endpoint,
            "version": self.version,
            "capabilities": self.capabilities,
            "skills": self.skills,
            "protocols": self.protocols,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "AgentCard":
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})


@dataclass
class A2ATask:
    """A task exchanged between agents."""

    task_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    sender_id: str = ""
    receiver_id: str = ""
    action: str = ""
    payload: dict[str, Any] = field(default_factory=dict)
    state: TaskState = TaskState.PENDING
    result: dict[str, Any] | None = None
    error: str | None = None
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "sender_id": self.sender_id,
            "receiver_id": self.receiver_id,
            "action": self.action,
            "payload": self.payload,
            "state": self.state.value,
            "result": self.result,
            "error": self.error,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "A2ATask":
        if "state" in data and isinstance(data["state"], str):
            data = dict(data)
            data["state"] = TaskState(data["state"])
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})


class A2ARegistry:
    """Registry of known remote agents."""

    def __init__(self, local_card: AgentCard | None = None):
        self._local_card = local_card
        self._peers: dict[str, AgentCard] = {}
        self._tasks: dict[str, A2ATask] = {}

    @property
    def local_card(self) -> AgentCard | None:
        return self._local_card

    def register_peer(self, card: AgentCard) -> None:
        self._peers[card.agent_id] = card
        logger.info("A2A: Registered peer %s at %s", card.name, card.endpoint)

    def unregister_peer(self, agent_id: str) -> bool:
        return self._peers.pop(agent_id, None) is not None

    def list_peers(self) -> list[dict[str, Any]]:
        return [card.to_dict() for card in self._peers.values()]

    def get_peer(self, agent_id: str) -> AgentCard | None:
        return self._peers.get(agent_id)

    def find_capable_peers(self, capability: str) -> list[AgentCard]:
        """Find peers that advertise a specific capability."""
        return [
            card for card in self._peers.values()
            if capability in card.capabilities or capability in card.skills
        ]

    def create_task(
        self,
        receiver_id: str,
        action: str,
        payload: dict[str, Any] | None = None,
    ) -> A2ATask:
        sender_id = self._local_card.agent_id if self._local_card else "unknown"
        task = A2ATask(
            sender_id=sender_id,
            receiver_id=receiver_id,
            action=action,
            payload=payload or {},
        )
        self._tasks[task.task_id] = task
        return task

    def update_task(
        self,
        task_id: str,
        state: TaskState | None = None,
        result: dict[str, Any] | None = None,
        error: str | None = None,
    ) -> A2ATask | None:
        task = self._tasks.get(task_id)
        if task is None:
            return None
        if state:
            task.state = state
        if result is not None:
            task.result = result
        if error is not None:
            task.error = error
        task.updated_at = time.time()
        return task

    def get_task(self, task_id: str) -> A2ATask | None:
        return self._tasks.get(task_id)

    def list_tasks(
        self,
        state: TaskState | None = None,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        tasks = list(self._tasks.values())
        if state:
            tasks = [t for t in tasks if t.state == state]
        tasks.sort(key=lambda t: t.updated_at, reverse=True)
        return [t.to_dict() for t in tasks[:limit]]

    def receive_task(self, data: dict[str, Any]) -> A2ATask:
        """Accept an incoming task from a remote agent."""
        task = A2ATask.from_dict(data)
        task.state = TaskState.PENDING
        self._tasks[task.task_id] = task
        logger.info(
            "A2A: Received task %s from %s (action=%s)",
            task.task_id, task.sender_id, task.action,
        )
        return task

    def to_dict(self) -> dict[str, Any]:
        return {
            "local_card": self._local_card.to_dict() if self._local_card else None,
            "peers": self.list_peers(),
            "pending_tasks": len([t for t in self._tasks.values() if t.state == TaskState.PENDING]),
            "total_tasks": len(self._tasks),
        }


async def send_task_to_peer(
    peer: AgentCard,
    task: A2ATask,
    timeout: float = 30.0,
) -> dict[str, Any]:
    """Send a task to a remote PyBot instance via HTTP POST."""
    import httpx

    url = f"{peer.endpoint.rstrip('/')}/api/a2a/tasks"
    async with httpx.AsyncClient(timeout=timeout) as client:
        resp = await client.post(url, json=task.to_dict())
        resp.raise_for_status()
        return resp.json()


async def fetch_peer_card(endpoint: str, timeout: float = 10.0) -> AgentCard | None:
    """Fetch the agent card from a remote PyBot instance."""
    import httpx

    url = f"{endpoint.rstrip('/')}/api/a2a/card"
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            return AgentCard.from_dict(resp.json())
    except Exception as exc:
        logger.warning("A2A: Failed to fetch card from %s: %s", endpoint, exc)
        return None
