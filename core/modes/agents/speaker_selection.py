"""Speaker selection strategies for multi-agent conversations.

Decides which agent speaks next in a group chat / collaboration node.
Inspired by AutoGen's SelectorGroupChat pattern.
"""

from __future__ import annotations

import logging
import random
from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

logger = logging.getLogger(__name__)

_SELECTOR_PROMPT = """You are a conversation coordinator.
Given the participants and the recent chat history, choose the SINGLE most appropriate next speaker.

Participants:
{participants}

Recent conversation:
{history}

Rules:
- Reply with ONLY the name of the next speaker, nothing else.
- Choose the participant whose expertise is most relevant to the current topic.
{repeat_rule}

Next speaker:"""


@dataclass
class Participant:
    name: str
    role: str = ""
    description: str = ""


@dataclass
class ChatMessage:
    speaker: str
    content: str


@runtime_checkable
class SpeakerSelector(Protocol):
    """Protocol for speaker selection strategies."""

    def select(
        self,
        participants: list[Participant],
        history: list[ChatMessage],
        context: dict[str, Any] | None = None,
    ) -> str:
        """Return the name of the next speaker."""
        ...


class RoundRobinSelector:
    """Cycles through participants in order."""

    def __init__(self):
        self._index = 0

    def select(
        self,
        participants: list[Participant],
        history: list[ChatMessage],
        context: dict[str, Any] | None = None,
    ) -> str:
        if not participants:
            raise ValueError("No participants")
        name = participants[self._index % len(participants)].name
        self._index += 1
        return name


class RandomSelector:
    """Picks a random participant."""

    def __init__(self, *, allow_repeat: bool = True):
        self._allow_repeat = allow_repeat

    def select(
        self,
        participants: list[Participant],
        history: list[ChatMessage],
        context: dict[str, Any] | None = None,
    ) -> str:
        if not participants:
            raise ValueError("No participants")
        if not self._allow_repeat and history:
            last_speaker = history[-1].speaker
            candidates = [p for p in participants if p.name != last_speaker]
            if candidates:
                return random.choice(candidates).name
        return random.choice(participants).name


class RuleBasedSelector:
    """Routes to a specific agent based on keyword matches in the last message."""

    def __init__(self, rules: dict[str, str], *, default: str | None = None):
        """rules: mapping of keyword -> agent_name."""
        self._rules = {k.lower(): v for k, v in rules.items()}
        self._default = default

    def select(
        self,
        participants: list[Participant],
        history: list[ChatMessage],
        context: dict[str, Any] | None = None,
    ) -> str:
        if not participants:
            raise ValueError("No participants")
        if history:
            last_msg = history[-1].content.lower()
            for keyword, agent in self._rules.items():
                if keyword in last_msg:
                    if any(p.name == agent for p in participants):
                        return agent
        if self._default and any(p.name == self._default for p in participants):
            return self._default
        return participants[0].name


class LLMSelector:
    """Uses an LLM to pick the best next speaker."""

    def __init__(
        self,
        llm: Any,
        *,
        allow_repeat: bool = False,
        max_attempts: int = 3,
        history_window: int = 10,
    ):
        self._llm = llm
        self._allow_repeat = allow_repeat
        self._max_attempts = max_attempts
        self._history_window = history_window
        self._fallback = RoundRobinSelector()

    def _build_prompt(
        self,
        participants: list[Participant],
        history: list[ChatMessage],
    ) -> str:
        parts_list = []
        for p in participants:
            desc = f" — {p.description}" if p.description else ""
            role = f" ({p.role})" if p.role else ""
            parts_list.append(f"- {p.name}{role}{desc}")
        participants_str = "\n".join(parts_list)

        recent = history[-self._history_window :]
        history_lines = []
        for msg in recent:
            preview = msg.content[:200]
            history_lines.append(f"{msg.speaker}: {preview}")
        history_str = "\n".join(history_lines) if history_lines else "(no messages yet)"

        repeat_rule = "" if self._allow_repeat else "- Do NOT choose the same speaker as the last message."

        return _SELECTOR_PROMPT.format(
            participants=participants_str,
            history=history_str,
            repeat_rule=repeat_rule,
        )

    def _parse_response(self, response: str, valid_names: set[str]) -> str | None:
        """Try to extract a valid participant name from LLM response."""
        cleaned = response.strip().strip('"').strip("'").strip()
        if cleaned in valid_names:
            return cleaned
        lower_map = {n.lower(): n for n in valid_names}
        if cleaned.lower() in lower_map:
            return lower_map[cleaned.lower()]
        for name in valid_names:
            if name.lower() in cleaned.lower():
                return name
        return None

    def select(
        self,
        participants: list[Participant],
        history: list[ChatMessage],
        context: dict[str, Any] | None = None,
    ) -> str:
        if not participants:
            raise ValueError("No participants")
        if len(participants) == 1:
            return participants[0].name

        valid_names = {p.name for p in participants}
        prompt = self._build_prompt(participants, history)

        for attempt in range(self._max_attempts):
            try:
                if hasattr(self._llm, "invoke"):
                    result = self._llm.invoke(prompt)
                    response_text = result.content if hasattr(result, "content") else str(result)
                elif callable(self._llm):
                    response_text = self._llm(prompt)
                else:
                    break

                parsed = self._parse_response(response_text, valid_names)
                if parsed:
                    if not self._allow_repeat and history and parsed == history[-1].speaker:
                        logger.debug("LLMSelector: repeated speaker %s, retrying", parsed)
                        continue
                    return parsed
                logger.debug("LLMSelector: could not parse %r (attempt %d)", response_text, attempt + 1)
            except Exception as exc:
                logger.warning("LLMSelector: LLM call failed (attempt %d): %s", attempt + 1, exc)

        logger.info("LLMSelector: falling back to RoundRobin")
        return self._fallback.select(participants, history, context)
