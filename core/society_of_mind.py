"""SocietyOfMind — nested multi-agent team pattern.

An inner team of agents collaborates on a task, producing a conversation
log. An outer "synthesizer" LLM then reads the entire inner conversation
and generates a single, cohesive final answer.

This enables hierarchical composition: a SocietyOfMind team can itself
be nested inside a larger conversation as a single participant.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from .event_bus import Event, EventType, event_bus
from .speaker_selection import (
    ChatMessage,
    Participant,
    RoundRobinSelector,
    SpeakerSelector,
)

logger = logging.getLogger(__name__)

_DEFAULT_SYNTHESIZER_PROMPT = """你是一个总结协调者。以下是一个专家团队围绕一个任务的内部讨论记录。

请综合所有专家的观点，生成一个完整、连贯、高质量的最终回答。
不要提及"讨论"或"专家"，直接给出最终答案。

任务: {task}

讨论记录:
{conversation}

最终回答:"""


@dataclass
class MindAgent:
    """Definition of an agent within a SocietyOfMind team."""

    name: str
    role: str = ""
    system_prompt: str = ""
    model: str = "gemini-3-flash-preview"
    temperature: float = 0.7
    description: str = ""


@dataclass
class SocietyConfig:
    """Configuration for a SocietyOfMind team."""

    max_rounds: int = 5
    synthesizer_prompt: str = _DEFAULT_SYNTHESIZER_PROMPT
    synthesizer_model: str = "gemini-3-flash-preview"
    synthesizer_temperature: float = 0.3
    include_task_in_each_round: bool = True


class SocietyOfMind:
    """Nested team pattern: inner agents discuss, outer LLM synthesizes.

    Usage:
        society = SocietyOfMind(
            name="research_team",
            agents=[MindAgent("analyst", ...), MindAgent("critic", ...)],
            llm_factory=my_factory,
        )
        result = society.run("Analyze the market trends")
    """

    def __init__(
        self,
        name: str,
        agents: list[MindAgent],
        llm_factory: Any,
        *,
        config: SocietyConfig | None = None,
        selector: SpeakerSelector | None = None,
    ):
        self.name = name
        self.agents = agents
        self._llm_factory = llm_factory
        self._config = config or SocietyConfig()
        self._selector = selector or RoundRobinSelector()

    def _invoke_agent(self, agent: MindAgent, prompt: str) -> str:
        try:
            llm = self._llm_factory(model=agent.model, temperature=agent.temperature)
            full_prompt = f"{agent.system_prompt}\n\n{prompt}" if agent.system_prompt else prompt
            result = llm.invoke(full_prompt)
            return result.content if hasattr(result, "content") else str(result)
        except Exception as exc:
            logger.warning("SocietyOfMind: agent %s failed: %s", agent.name, exc)
            return f"[{agent.name} 出错: {exc}]"

    def _run_inner_discussion(self, task: str, context: str = "") -> list[ChatMessage]:
        """Run the inner team discussion."""
        participants = [Participant(name=a.name, role=a.role, description=a.description) for a in self.agents]
        agent_map = {a.name: a for a in self.agents}

        history: list[ChatMessage] = []
        initial = f"任务: {task}"
        if context:
            initial = f"背景: {context}\n\n{initial}"
        history.append(ChatMessage(speaker="coordinator", content=initial))

        for _round_num in range(self._config.max_rounds):
            speaker_name = self._selector.select(participants, history)
            agent = agent_map.get(speaker_name)
            if agent is None:
                continue

            recent_msgs = history[-10:]
            conversation_text = "\n".join(f"[{m.speaker}]: {m.content}" for m in recent_msgs)

            prompt_parts = [f"团队讨论记录:\n{conversation_text}"]
            if self._config.include_task_in_each_round:
                prompt_parts.insert(0, f"任务: {task}")
            prompt_parts.append(f"\n请以 {speaker_name}（{agent.role}）的身份发表你的专业观点。")

            response = self._invoke_agent(agent, "\n\n".join(prompt_parts))
            history.append(ChatMessage(speaker=speaker_name, content=response))

        return history

    def _synthesize(self, task: str, conversation: list[ChatMessage]) -> str:
        """Have the outer LLM synthesize all inner messages into a final answer."""
        conv_text = "\n\n".join(f"[{m.speaker}]: {m.content}" for m in conversation if m.speaker != "coordinator")
        prompt = self._config.synthesizer_prompt.replace("{task}", task).replace("{conversation}", conv_text)

        try:
            llm = self._llm_factory(
                model=self._config.synthesizer_model,
                temperature=self._config.synthesizer_temperature,
            )
            result = llm.invoke(prompt)
            return result.content if hasattr(result, "content") else str(result)
        except Exception as exc:
            logger.warning("SocietyOfMind: synthesizer failed: %s", exc)
            if conversation:
                return conversation[-1].content
            return f"[综合失败: {exc}]"

    def run(self, task: str, context: str = "") -> str:
        """Execute the full SocietyOfMind pattern.

        Returns the synthesized final answer.
        """
        event_bus.emit(
            Event(
                type=EventType.AGENT_START,
                payload={
                    "team": self.name,
                    "agent_count": len(self.agents),
                    "max_rounds": self._config.max_rounds,
                    "mode": "society_of_mind",
                },
                source=f"society_{self.name}",
            )
        )

        conversation = self._run_inner_discussion(task, context)
        final = self._synthesize(task, conversation)

        event_bus.emit(
            Event(
                type=EventType.AGENT_END,
                payload={
                    "team": self.name,
                    "discussion_messages": len(conversation),
                    "success": True,
                    "mode": "society_of_mind",
                },
                source=f"society_{self.name}",
            )
        )

        return final

    def get_conversation_log(self, task: str, context: str = "") -> dict[str, Any]:
        """Run and return both the conversation log and the final answer."""
        conversation = self._run_inner_discussion(task, context)
        final = self._synthesize(task, conversation)
        return {
            "task": task,
            "conversation": [{"speaker": m.speaker, "content": m.content} for m in conversation],
            "final_answer": final,
            "agent_count": len(self.agents),
            "message_count": len(conversation),
        }
