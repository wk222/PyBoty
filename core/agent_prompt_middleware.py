"""LangChain middleware for dynamic system-prompt sections."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from langchain_core.messages import SystemMessage

try:
    from langchain.agents.middleware import AgentMiddleware
    from langchain.agents.middleware.types import ModelRequest, ModelResponse

    LANGCHAIN_1_AVAILABLE = True
except ImportError:
    LANGCHAIN_1_AVAILABLE = False
    AgentMiddleware = object
    ModelRequest = object
    ModelResponse = object


def append_to_system_message(system_message: SystemMessage | None, text: str) -> SystemMessage:
    """Append text to a system message without discarding existing content."""
    if system_message is None:
        return SystemMessage(content=text)

    metadata: dict[str, Any] = {}
    for key in ("additional_kwargs", "response_metadata", "id", "name"):
        value = getattr(system_message, key, None)
        if value not in (None, {}):
            metadata[key] = value

    content_blocks = list(getattr(system_message, "content_blocks", []))
    if content_blocks:
        if text:
            content_blocks.append({"type": "text", "text": f"\n\n{text}"})
        return SystemMessage(content_blocks=content_blocks, **metadata)

    current_text = system_message.text
    if current_text:
        return SystemMessage(content=f"{current_text}\n\n{text}", **metadata)
    return SystemMessage(content=text, **metadata)


class PromptSectionMiddleware(AgentMiddleware if LANGCHAIN_1_AVAILABLE else object):
    """Append dynamically rendered prompt sections at model-call time."""

    def __init__(self, *, name: str, prompt_builder: Callable[[], str]):
        self._name = name
        self._prompt_builder = prompt_builder

    @property
    def name(self) -> str:
        return self._name

    def wrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], ModelResponse],
    ) -> ModelResponse:
        return handler(self._inject_prompt(request))

    async def awrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], Awaitable[ModelResponse]],
    ) -> ModelResponse:
        return await handler(self._inject_prompt(request))

    def _inject_prompt(self, request: ModelRequest) -> ModelRequest:
        prompt_section = self._prompt_builder().strip()
        if not prompt_section:
            return request
        return request.override(system_message=append_to_system_message(request.system_message, prompt_section))
