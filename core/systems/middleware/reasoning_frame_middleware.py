"""Structured reasoning frame for agent decision making.

Injects a reasoning scaffold into the system prompt that guides the model
to decompose its thinking into explicit phases: observation, analysis,
self-critique, plan, and action rationale.  This improves decision quality
on complex multi-step tasks while making the agent's thought process
transparent and debuggable.

The frame is optional and can be toggled per-agent via config.  It adds
~200 tokens to the system prompt but typically *saves* tokens overall by
reducing false starts and dead-end explorations.

"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
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

from .agent_prompt_middleware import append_to_system_message

logger = logging.getLogger(__name__)

REASONING_FRAME_PROMPT = """## Structured Reasoning Frame

Before selecting your next action, work through these phases internally:

1. **Observation** — What is the current state? What did the last action produce?
2. **Analysis** — What patterns or issues do you see? What constraints apply?
3. **Self-Critique** — Is your current approach working? Are you making progress or spinning wheels? What assumptions might be wrong?
4. **Plan** — What are the next 2-3 concrete steps? What's the fallback if this fails?
5. **Action Rationale** — Why this specific tool/action and not alternatives?

You do NOT need to write out all 5 phases explicitly in your response.
Use them as an internal checklist. When the reasoning is non-trivial,
briefly surface your analysis and rationale so the user can follow your logic.
"""

REASONING_FRAME_STRICT_PROMPT = """## Structured Reasoning Frame (Strict Mode)

You MUST structure your thinking before EVERY tool call. Output a brief
`<reasoning>` block before each action:

```
<reasoning>
Observation: [1 sentence — what you see]
Analysis: [1-2 sentences — what it means]
Self-Critique: [1 sentence — potential issues with your approach]
Plan: [numbered list of 2-3 next steps]
Action: [which tool and why]
</reasoning>
```

Then proceed with the tool call. Keep each reasoning block under 100 words.
"""


@dataclass
class ReasoningFrameConfig:
    enabled: bool = True
    strict_mode: bool = False
    min_messages_to_activate: int = 2
    complexity_keywords: tuple[str, ...] = (
        "debug", "fix", "error", "refactor", "migrate", "design",
        "architect", "optimize", "investigate", "complex", "multi-step",
        "分析", "调试", "修复", "重构", "设计", "优化",
    )
    auto_activate_on_complexity: bool = True


class ReasoningFrameMiddleware(AgentMiddleware if _HAS_LC else object):  # type: ignore[misc]
    """Inject structured reasoning scaffold into agent prompts."""

    def __init__(self, *, config: ReasoningFrameConfig | None = None):
        self._config = config or ReasoningFrameConfig()
        self._activated: bool = False
        self._total_injections: int = 0

    @property
    def name(self) -> str:
        return "ReasoningFrameMiddleware"

    def _should_activate(self, messages: list[Any]) -> bool:
        """Decide whether to inject the reasoning frame."""
        if not self._config.enabled:
            return False

        if len(messages) < self._config.min_messages_to_activate:
            return False

        if self._config.auto_activate_on_complexity:
            for msg in messages[-4:]:
                content = getattr(msg, "content", "")
                if isinstance(content, str):
                    lower = content.lower()
                    if any(kw in lower for kw in self._config.complexity_keywords):
                        return True

        tool_call_count = 0
        for msg in messages:
            for _ in getattr(msg, "tool_calls", None) or []:
                tool_call_count += 1
        if tool_call_count >= 4:
            return True

        return self._activated

    def _process_request(self, request: Any) -> Any:
        messages = list(request.messages)
        if not self._should_activate(messages):
            return request

        self._activated = True
        self._total_injections += 1

        prompt = (
            REASONING_FRAME_STRICT_PROMPT
            if self._config.strict_mode
            else REASONING_FRAME_PROMPT
        )
        return request.override(
            system_message=append_to_system_message(request.system_message, prompt),
        )

    def wrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], ModelResponse],
    ) -> ModelResponse:
        return handler(self._process_request(request))

    async def awrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], Awaitable[ModelResponse]],
    ) -> ModelResponse:
        return await handler(self._process_request(request))

    def get_stats(self) -> dict[str, Any]:
        return {
            "enabled": self._config.enabled,
            "strict_mode": self._config.strict_mode,
            "activated": self._activated,
            "total_injections": self._total_injections,
        }
