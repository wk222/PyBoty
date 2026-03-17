"""LangChain middleware for post-invoke long-term memory extraction.

Replaces the legacy MiddlewareStack-based MemoryMiddleware with a proper
LangChain ``AgentMiddleware`` that runs in the unified pipeline.

Uses ``wrap_model_call`` to capture the model response and extract key
facts from the latest exchange into MEMORY.md.
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
    """Extract key facts from conversations and persist to MEMORY.md."""

    def __init__(self, memory_manager: Any) -> None:
        self._memory = memory_manager

    @property
    def name(self) -> str:
        return "LCMemoryMiddleware"

    def wrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], ModelResponse],
    ) -> ModelResponse:
        response = handler(request)
        self._extract_memories(request.messages)
        return response

    async def awrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], Awaitable[ModelResponse]],
    ) -> ModelResponse:
        response = await handler(request)
        self._extract_memories(request.messages)
        return response

    def _extract_memories(self, messages: list[Any]) -> None:
        if not messages or len(messages) < 2:
            return
        try:
            from .memory_manager import extract_key_facts

            last_two = messages[-2:]
            conversation = []
            for m in last_two:
                content = getattr(m, "content", "")
                if not content:
                    continue
                role = "user" if getattr(m, "type", "") == "human" else "assistant"
                conversation.append({"role": role, "content": content})
            if len(conversation) >= 2:
                facts = extract_key_facts(conversation)
                for fact in facts:
                    self._memory.append_memory(fact["section"], fact["content"])
        except Exception as exc:
            logger.debug("Memory extraction failed: %s", exc)
