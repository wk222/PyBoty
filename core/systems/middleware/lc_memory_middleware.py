"""LangChain middleware for memory lifecycle management.

Provides two memory lifecycle hooks:
  - **Auto-recall (before_agent_start)**: Inject relevant memories into the
    system prompt before the model sees the user's message.
  - **Auto-capture (agent_end)**: Extract key facts from the conversation
    after the model responds and persist them to long-term memory.

Uses ``wrap_model_call`` to intercept the model pipeline.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import Any

try:
    from langchain.agents.middleware import AgentMiddleware
    from langchain.agents.middleware.types import ModelRequest, ModelResponse

    _HAS_LC = True
except ImportError:
    _HAS_LC = False
    AgentMiddleware = object  # type: ignore[assignment,misc]
    ModelRequest = object  # type: ignore[assignment,misc]
    ModelResponse = object  # type: ignore[assignment,misc]

logger = logging.getLogger(__name__)


class LCMemoryMiddleware(AgentMiddleware if _HAS_LC else object):  # type: ignore[misc]
    """Auto-recall before model call + auto-capture after response."""

    def __init__(self, memory_manager: Any, *, auto_recall: bool = True, auto_capture: bool = True) -> None:
        self._memory = memory_manager
        self._auto_recall = auto_recall
        self._auto_capture = auto_capture

    @property
    def name(self) -> str:
        return "LCMemoryMiddleware"

    def wrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], ModelResponse],
    ) -> ModelResponse:
        self._inject_recall(request)
        response = handler(request)
        self._extract_memories(request.messages)
        return response

    async def awrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], Awaitable[ModelResponse]],
    ) -> ModelResponse:
        self._inject_recall(request)
        response = await handler(request)
        self._extract_memories(request.messages)
        return response

    def _inject_recall(self, request: ModelRequest) -> None:
        """Auto-recall: inject relevant memories into the system prompt."""
        if not self._auto_recall:
            return
        if not hasattr(self._memory, "auto_recall"):
            return
        try:
            user_text = self._get_latest_user_text(request.messages)
            if not user_text:
                return
            recalled = self._memory.auto_recall(user_text, top_k=5)
            if recalled and request.messages:
                first = request.messages[0]
                existing = getattr(first, "content", "")
                if existing and getattr(first, "type", "") == "system":
                    first.content = f"{existing}\n\n{recalled}"
                    logger.debug("Auto-recalled %d chars of memory context", len(recalled))
        except Exception as exc:
            logger.debug("Auto-recall failed: %s", exc)

    @staticmethod
    def _get_latest_user_text(messages: list[Any]) -> str:
        """Extract the latest user message text for recall queries."""
        for m in reversed(messages):
            if getattr(m, "type", "") == "human":
                content = getattr(m, "content", "")
                if content:
                    return content[:500]
        return ""

    def _extract_memories(self, messages: list[Any]) -> None:
        """Auto-capture: extract key facts from recent conversation."""
        if not self._auto_capture:
            return
        if not messages or len(messages) < 2:
            return

        if hasattr(self._memory, "auto_capture"):
            try:
                conversation = self._build_conversation(messages[-4:])
                if len(conversation) >= 2:
                    self._memory.auto_capture(conversation)
            except Exception as exc:
                logger.debug("Auto-capture failed: %s", exc)
        else:
            self._legacy_extract(messages)

    def _legacy_extract(self, messages: list[Any]) -> None:
        """Fallback extraction for non-semantic memory managers."""
        try:
            from core.systems.memory.memory_manager import extract_key_facts

            conversation = self._build_conversation(messages[-2:])
            if len(conversation) >= 2:
                facts = extract_key_facts(conversation)
                for fact in facts:
                    self._memory.append_memory(fact["section"], fact["content"])
        except Exception as exc:
            logger.debug("Legacy memory extraction failed: %s", exc)

    @staticmethod
    def _build_conversation(messages: list[Any]) -> list[dict[str, str]]:
        conversation: list[dict[str, str]] = []
        for m in messages:
            content = getattr(m, "content", "")
            if not content:
                continue
            role = "user" if getattr(m, "type", "") == "human" else "assistant"
            conversation.append({"role": role, "content": content})
        return conversation
