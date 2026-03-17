"""Persistence helpers for persisted subagent definitions."""

from __future__ import annotations

import json
import shutil
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .agent_capability_profile import AgentCapabilityProfile
from .agent_middleware_profile import AgentMiddlewareProfile


@dataclass
class AgentDefinition:
    """Persisted subagent definition."""

    name: str
    role: str
    description: str
    system_prompt: str
    tools: list[str] = field(default_factory=list)
    model: str = "gemini-3-flash-preview"
    temperature: float = 0.7
    capabilities: list[str] = field(default_factory=list)
    capability_profile: dict[str, Any] = field(default_factory=dict)
    middleware_profile: dict[str, Any] = field(default_factory=dict)
    created_at: float = field(default_factory=time.time)
    usage_count: int = 0
    enabled: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "role": self.role,
            "description": self.description,
            "system_prompt": self.system_prompt,
            "tools": self.tools,
            "model": self.model,
            "temperature": self.temperature,
            "capabilities": self.capabilities,
            "capability_profile": self.capability_profile,
            "middleware_profile": self.middleware_profile,
            "created_at": self.created_at,
            "usage_count": self.usage_count,
            "enabled": self.enabled,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> AgentDefinition:
        return cls(
            name=str(data.get("name", "")),
            role=str(data.get("role", "")),
            description=str(data.get("description", "")),
            system_prompt=str(data.get("system_prompt", "")),
            tools=list(data.get("tools", [])),
            model=str(data.get("model", "gemini-3-flash-preview")),
            temperature=float(data.get("temperature", 0.7)),
            capabilities=list(data.get("capabilities", [])),
            capability_profile=AgentCapabilityProfile.from_value(data.get("capability_profile")).to_dict(),
            middleware_profile=AgentMiddlewareProfile.from_value(data.get("middleware_profile")).to_dict(),
            created_at=float(data.get("created_at", time.time())),
            usage_count=int(data.get("usage_count", 0)),
            enabled=bool(data.get("enabled", True)),
        )


class AgentStorage:
    """Manage persisted subagent definitions in per-agent folders."""

    def __init__(self, base_dir: str = "agents_workspace"):
        self.base_dir_path = Path(base_dir).resolve()
        self.base_dir = str(self.base_dir_path)
        self.agents: dict[str, AgentDefinition] = {}
        self._ensure_base_dir()
        self.reload()

    def _ensure_base_dir(self) -> None:
        self.base_dir_path.mkdir(parents=True, exist_ok=True)

    def _agent_dir(self, name: str) -> Path:
        return self.base_dir_path / name

    def _config_path(self, name: str) -> Path:
        return self._agent_dir(name) / "agent_config.json"

    def _tools_dir(self, name: str) -> Path:
        return self._agent_dir(name) / "tools"

    def tools_dir_for(self, name: str) -> Path:
        return self._tools_dir(name)

    @staticmethod
    def _read_json(path: Path) -> dict[str, Any]:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError(f"Expected object JSON in {path}")
        return data

    @staticmethod
    def _write_json(path: Path, payload: dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = path.with_name(f"{path.name}.{uuid.uuid4().hex}.tmp")
        tmp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp_path.replace(path)

    def reload(self) -> None:
        self.agents = {}
        if not self.base_dir_path.exists():
            return

        for agent_dir in sorted(path for path in self.base_dir_path.iterdir() if path.is_dir()):
            config_path = agent_dir / "agent_config.json"
            if not config_path.exists():
                continue
            try:
                agent = AgentDefinition.from_dict(self._read_json(config_path))
            except Exception as exc:
                print(f"Error loading agent from {config_path}: {exc}")
                continue
            self.agents[agent.name] = agent

    def _save_agent(self, agent: AgentDefinition) -> None:
        self._agent_dir(agent.name).mkdir(parents=True, exist_ok=True)
        self._tools_dir(agent.name).mkdir(parents=True, exist_ok=True)
        self._write_json(self._config_path(agent.name), agent.to_dict())

    def add_agent(self, definition: AgentDefinition) -> bool:
        if definition.name in self.agents:
            return False
        self.agents[definition.name] = definition
        self._save_agent(definition)
        return True

    def get_agent(self, name: str) -> AgentDefinition | None:
        return self.agents.get(name)

    def remove_agent(self, name: str) -> bool:
        if name not in self.agents:
            return False
        del self.agents[name]
        agent_dir = self._agent_dir(name)
        if agent_dir.exists():
            try:
                shutil.rmtree(agent_dir)
            except Exception as exc:
                print(f"Error removing agent directory {agent_dir}: {exc}")
        return True

    def update_agent(self, name: str, updates: dict[str, Any]) -> bool:
        agent = self.agents.get(name)
        if agent is None:
            return False

        for key, value in updates.items():
            if hasattr(agent, key):
                setattr(agent, key, value)
        self._save_agent(agent)
        return True

    def increment_usage(self, name: str) -> None:
        agent = self.agents.get(name)
        if agent is None:
            return
        agent.usage_count += 1
        self._save_agent(agent)

    def toggle_agent(self, name: str, enabled: bool) -> bool:
        return self.update_agent(name, {"enabled": enabled})

    def add_tool_to_agent(self, agent_name: str, tool_name: str) -> bool:
        agent = self.agents.get(agent_name)
        if agent is None:
            return False
        if tool_name not in agent.tools:
            agent.tools.append(tool_name)
            self._save_agent(agent)
        return True

    def remove_tool_from_agent(self, agent_name: str, tool_name: str) -> bool:
        agent = self.agents.get(agent_name)
        if agent is None or tool_name not in agent.tools:
            return False
        agent.tools.remove(tool_name)
        self._save_agent(agent)
        return True

    def list_agents(self) -> dict[str, str]:
        return {name: agent.description for name, agent in self.agents.items()}

    def get_agents_by_capability(self, capability: str) -> list[AgentDefinition]:
        return [agent for agent in self.agents.values() if capability in agent.capabilities]

    def to_dict(self) -> dict[str, Any]:
        return {
            "agents": {name: agent.to_dict() for name, agent in self.agents.items()},
            "count": len(self.agents),
            "timestamp": time.time(),
            "base_dir": self.base_dir,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> AgentStorage:
        storage = cls(base_dir=str(data.get("base_dir", "agents_workspace")))
        for name, agent_dict in data.get("agents", {}).items():
            if name in storage.agents:
                continue
            agent = AgentDefinition.from_dict(agent_dict)
            storage.agents[name] = agent
            storage._save_agent(agent)
        return storage

    def export_to_json(self, filepath: str | Path) -> None:
        Path(filepath).write_text(json.dumps(self.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")

    @classmethod
    def import_from_json(cls, filepath: str | Path) -> AgentStorage:
        data = json.loads(Path(filepath).read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError(f"Expected object JSON in {filepath}")
        return cls.from_dict(data)


@dataclass
class AgentContext:
    """Serializable context snapshot for agent coordination state."""

    agent_definitions: dict[str, dict[str, Any]] = field(default_factory=dict)
    agent_usage: dict[str, int] = field(default_factory=dict)
    active_agents: list[str] = field(default_factory=list)
    communication_history: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "agent_definitions": self.agent_definitions,
            "agent_usage": self.agent_usage,
            "active_agents": self.active_agents,
            "communication_history": self.communication_history[-100:],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> AgentContext:
        if not isinstance(data, dict):
            return cls()
        return cls(
            agent_definitions=data.get("agent_definitions", {}),
            agent_usage=data.get("agent_usage", {}),
            active_agents=data.get("active_agents", []),
            communication_history=data.get("communication_history", []),
        )

    def record_communication(self, from_agent: str, to_agent: str, message: str, response: str) -> None:
        self.communication_history.append(
            {
                "from": from_agent,
                "to": to_agent,
                "message": message,
                "response": response,
                "timestamp": time.time(),
            }
        )
