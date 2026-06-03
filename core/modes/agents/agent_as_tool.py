"""Wrap an agent or a team of agents as a regular LangChain tool.

This enables nested composition: an outer agent can invoke an inner
agent (or an entire multi-agent team) through its tool belt, just
like calling any other function.

Patterns supported:
  - AgentTool: single agent callable as a tool
  - TeamTool: a group of agents with a coordinator, callable as a tool
"""

from __future__ import annotations

import json
import logging
from typing import Any

from langchain.tools import BaseTool
from pydantic import BaseModel, ConfigDict, Field

from core.systems.runtime.event_bus import Event, EventType, event_bus

from .speaker_selection import (
    ChatMessage,
    Participant,
    RoundRobinSelector,
    SpeakerSelector,
)

logger = logging.getLogger(__name__)


class AgentToolInput(BaseModel):
    """Input for calling an agent-as-tool."""

    request: str = Field(description="发送给智能体的请求或任务描述")
    context: str = Field(default="", description="可选的背景上下文信息")


class AgentTool(BaseTool):
    """Wraps a single agent as a callable tool.

    Any outer agent can include this in its toolbelt and invoke the
    wrapped agent transparently — the caller only sees a tool that
    accepts a request string and returns a response string.
    """

    name: str = "agent_tool"
    description: str = "调用一个智能体来处理请求"
    args_schema: type[BaseModel] = AgentToolInput

    agent_name: str = Field(default="")
    agent_role: str = Field(default="")
    system_prompt: str = Field(default="")
    llm_factory: Any = Field(default=None, exclude=True)
    model: str = Field(default="gemini-3-flash-preview")
    temperature: float = Field(default=0.7)

    model_config = ConfigDict(arbitrary_types_allowed=True)

    def __init__(
        self,
        agent_name: str,
        *,
        agent_role: str = "",
        system_prompt: str = "",
        llm_factory: Any = None,
        model: str = "gemini-3-flash-preview",
        temperature: float = 0.7,
        tool_description: str | None = None,
        **kwargs,
    ):
        tool_name = f"agent_{agent_name}"
        desc = tool_description or f"调用 {agent_name}（{agent_role}）来处理任务"
        super().__init__(
            name=tool_name,
            description=desc,
            agent_name=agent_name,
            agent_role=agent_role,
            system_prompt=system_prompt,
            llm_factory=llm_factory,
            model=model,
            temperature=temperature,
            **kwargs,
        )

    def _run(self, request: str, context: str = "") -> str:
        event_bus.emit(
            Event(
                type=EventType.AGENT_START,
                payload={"agent_name": self.agent_name, "request": request[:200], "mode": "agent_as_tool"},
                source=self.name,
            )
        )

        try:
            if self.llm_factory is None:
                return json.dumps({"success": False, "error": "llm_factory not configured"})

            llm = self.llm_factory(model=self.model, temperature=self.temperature)

            prompt_parts = []
            if self.system_prompt:
                prompt_parts.append(self.system_prompt)
            if context:
                prompt_parts.append(f"\n背景信息:\n{context}")
            prompt_parts.append(f"\n请求:\n{request}")

            response = llm.invoke("\n".join(prompt_parts))
            answer = response.content if hasattr(response, "content") else str(response)

            event_bus.emit(
                Event(
                    type=EventType.AGENT_END,
                    payload={"agent_name": self.agent_name, "success": True, "mode": "agent_as_tool"},
                    source=self.name,
                )
            )

            return answer

        except Exception as exc:
            event_bus.emit(
                Event(
                    type=EventType.AGENT_END,
                    payload={
                        "agent_name": self.agent_name,
                        "success": False,
                        "error": str(exc),
                        "mode": "agent_as_tool",
                    },
                    source=self.name,
                )
            )
            return json.dumps({"success": False, "error": str(exc)})


class TeamToolInput(BaseModel):
    """Input for calling a team-as-tool."""

    task: str = Field(description="要团队完成的任务描述")
    context: str = Field(default="", description="可选的任务上下文")


class TeamTool(BaseTool):
    """Wraps a team of agents as a single callable tool.

    The team coordinates internally via a SpeakerSelector, taking
    turns to process the task until max_rounds or convergence.
    The final consolidated output is returned to the caller.
    """

    name: str = "team_tool"
    description: str = "调用一个智能体团队来协作完成任务"
    args_schema: type[BaseModel] = TeamToolInput

    agents: list[dict[str, Any]] = Field(default_factory=list)
    llm_factory: Any = Field(default=None, exclude=True)
    selector: Any = Field(default=None, exclude=True)
    max_rounds: int = Field(default=5)
    summarizer_prompt: str = Field(default="")

    model_config = ConfigDict(arbitrary_types_allowed=True)

    def __init__(
        self,
        team_name: str,
        agents: list[dict[str, Any]],
        *,
        llm_factory: Any = None,
        selector: SpeakerSelector | None = None,
        max_rounds: int = 5,
        summarizer_prompt: str = "",
        tool_description: str | None = None,
        **kwargs,
    ):
        tool_name = f"team_{team_name}"
        desc = tool_description or f"调用 {team_name} 团队协作完成任务"
        super().__init__(
            name=tool_name,
            description=desc,
            agents=agents,
            llm_factory=llm_factory,
            selector=selector,
            max_rounds=max_rounds,
            summarizer_prompt=summarizer_prompt,
            **kwargs,
        )

    def _get_participants(self) -> list[Participant]:
        return [
            Participant(
                name=a["name"],
                role=a.get("role", ""),
                description=a.get("description", ""),
            )
            for a in self.agents
        ]

    def _get_selector(self) -> SpeakerSelector:
        if self.selector is not None:
            return self.selector
        return RoundRobinSelector()

    def _invoke_agent(self, agent_def: dict[str, Any], prompt: str) -> str:
        if self.llm_factory is None:
            return "[Error: llm_factory not configured]"
        try:
            llm = self.llm_factory(
                model=agent_def.get("model", "gemini-3-flash-preview"),
                temperature=agent_def.get("temperature", 0.7),
            )
            sys_prompt = agent_def.get("system_prompt", "")
            full_prompt = f"{sys_prompt}\n\n{prompt}" if sys_prompt else prompt
            result = llm.invoke(full_prompt)
            return result.content if hasattr(result, "content") else str(result)
        except Exception as exc:
            return f"[Agent {agent_def['name']} error: {exc}]"

    def _run(self, task: str, context: str = "") -> str:
        event_bus.emit(
            Event(
                type=EventType.AGENT_START,
                payload={
                    "team": self.name,
                    "task": task[:200],
                    "agent_count": len(self.agents),
                    "mode": "team_as_tool",
                },
                source=self.name,
            )
        )

        participants = self._get_participants()
        selector = self._get_selector()
        agent_map = {a["name"]: a for a in self.agents}

        history: list[ChatMessage] = []
        initial_msg = task
        if context:
            initial_msg = f"背景: {context}\n\n任务: {task}"
        history.append(ChatMessage(speaker="coordinator", content=initial_msg))

        for _round_num in range(self.max_rounds):
            speaker_name = selector.select(participants, history)
            agent_def = agent_map.get(speaker_name)
            if agent_def is None:
                continue

            conversation_text = "\n".join(f"[{m.speaker}]: {m.content}" for m in history[-10:])
            prompt = f"对话记录:\n{conversation_text}\n\n请以 {speaker_name} 的身份回应。"

            response = self._invoke_agent(agent_def, prompt)
            history.append(ChatMessage(speaker=speaker_name, content=response))

        if self.summarizer_prompt and self.llm_factory:
            try:
                llm = self.llm_factory(model="gemini-3-flash-preview", temperature=0.3)
                all_msgs = "\n\n".join(f"[{m.speaker}]: {m.content}" for m in history)
                summary_prompt = f"{self.summarizer_prompt}\n\n对话记录:\n{all_msgs}"
                result = llm.invoke(summary_prompt)
                final = result.content if hasattr(result, "content") else str(result)
            except Exception:
                final = history[-1].content if history else ""
        else:
            final = history[-1].content if history else ""

        event_bus.emit(
            Event(
                type=EventType.AGENT_END,
                payload={"team": self.name, "success": True, "rounds": len(history) - 1, "mode": "team_as_tool"},
                source=self.name,
            )
        )

        return final


def create_agent_tool(
    name: str,
    role: str,
    system_prompt: str,
    llm_factory: Any,
    *,
    model: str = "gemini-3-flash-preview",
    temperature: float = 0.7,
    description: str | None = None,
) -> AgentTool:
    """Convenience factory for creating an AgentTool."""
    return AgentTool(
        agent_name=name,
        agent_role=role,
        system_prompt=system_prompt,
        llm_factory=llm_factory,
        model=model,
        temperature=temperature,
        tool_description=description,
    )


def create_team_tool(
    name: str,
    agents: list[dict[str, Any]],
    llm_factory: Any,
    *,
    selector: SpeakerSelector | None = None,
    max_rounds: int = 5,
    summarizer_prompt: str = "",
    description: str | None = None,
) -> TeamTool:
    """Convenience factory for creating a TeamTool."""
    return TeamTool(
        team_name=name,
        agents=agents,
        llm_factory=llm_factory,
        selector=selector,
        max_rounds=max_rounds,
        summarizer_prompt=summarizer_prompt,
        tool_description=description,
    )
