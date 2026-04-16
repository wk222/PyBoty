"""Mock LLM harness for integration tests without real API calls.

Provides deterministic, scriptable LLM responses so integration tests
can exercise the full agent pipeline (prompt → LLM → tool → response)
without network access or API keys.

Usage
-----
    from tests.mock_llm import MockLLM, MockLLMFactory

    llm = MockLLM(responses=["Hello!", "Goodbye!"])
    result = llm.invoke("prompt")          # → AIMessage(content="Hello!")
    result = llm.invoke("another prompt")  # → AIMessage(content="Goodbye!")

    # Use as a factory for agent construction
    factory = MockLLMFactory(default_response="OK")
    agent = build_agent(llm_factory=factory)
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Callable, Sequence


@dataclass
class AIMessageCompat:
    """Minimal AIMessage-compatible object for tests."""
    content: str
    additional_kwargs: dict = field(default_factory=dict)
    tool_calls: list = field(default_factory=list)

    @property
    def type(self) -> str:
        return "ai"


@dataclass
class ToolCallCompat:
    """Minimal ToolCall-compatible dict for simulating tool calls."""
    name: str
    args: dict
    id: str = "mock_call_0"

    def to_dict(self) -> dict:
        return {"name": self.name, "args": self.args, "id": self.id}


class MockLLM:
    """A scriptable mock LLM that returns pre-defined responses.

    Supports three modes:

    1. **Sequential** – provide a list of responses; each ``invoke()``
       returns the next one in order.
    2. **Pattern-matched** – provide ``pattern_responses`` mapping regex
       patterns to response strings. First matching pattern wins.
    3. **Default** – a single string used when both lists are exhausted
       or nothing matches.

    All invocations are recorded in ``self.history`` for assertion.
    """

    def __init__(
        self,
        responses: Sequence[str | AIMessageCompat] | None = None,
        default_response: str = "Mock response",
        pattern_responses: dict[str, str] | None = None,
        tool_call_responses: dict[str, list[ToolCallCompat]] | None = None,
    ):
        self._responses = list(responses or [])
        self._default = default_response
        self._patterns = pattern_responses or {}
        self._tool_call_patterns = tool_call_responses or {}
        self._index = 0
        self.history: list[dict[str, Any]] = []

    def invoke(self, prompt: Any, **kwargs: Any) -> AIMessageCompat:
        prompt_str = str(prompt)
        self.history.append({"prompt": prompt_str, "kwargs": kwargs})

        for pattern, tool_calls in self._tool_call_patterns.items():
            if re.search(pattern, prompt_str, re.IGNORECASE):
                return AIMessageCompat(
                    content="",
                    tool_calls=[tc.to_dict() for tc in tool_calls],
                )

        for pattern, response in self._patterns.items():
            if re.search(pattern, prompt_str, re.IGNORECASE):
                return self._wrap(response)

        if self._index < len(self._responses):
            resp = self._responses[self._index]
            self._index += 1
            return self._wrap(resp)

        return self._wrap(self._default)

    async def ainvoke(self, prompt: Any, **kwargs: Any) -> AIMessageCompat:
        return self.invoke(prompt, **kwargs)

    def bind_tools(self, tools: list, **kwargs: Any) -> "MockLLM":
        return self

    @staticmethod
    def _wrap(resp: str | AIMessageCompat) -> AIMessageCompat:
        if isinstance(resp, AIMessageCompat):
            return resp
        return AIMessageCompat(content=resp)

    @property
    def call_count(self) -> int:
        return len(self.history)

    def reset(self) -> None:
        self._index = 0
        self.history.clear()


class MockLLMFactory:
    """Callable factory that returns MockLLM instances.

    Behaves like ``llm_factory(model=..., temperature=...)`` used in PyBot's
    codebase, but always returns a pre-configured MockLLM.
    """

    def __init__(
        self,
        default_response: str = "Mock response",
        responses: Sequence[str] | None = None,
        pattern_responses: dict[str, str] | None = None,
    ):
        self._default = default_response
        self._responses = responses
        self._patterns = pattern_responses
        self.created: list[MockLLM] = []

    def __call__(self, model: str = "", temperature: float = 0.7, **kw: Any) -> MockLLM:
        llm = MockLLM(
            responses=list(self._responses) if self._responses else None,
            default_response=self._default,
            pattern_responses=dict(self._patterns) if self._patterns else None,
        )
        self.created.append(llm)
        return llm


def mock_llm_caller(
    response: str = "Mock LLM answer",
    pattern_responses: dict[str, str] | None = None,
) -> Callable[[str, str], str]:
    """Create a simple ``(system, user) -> str`` LLM caller for MemoryDistill etc.

    The returned callable records all invocations for later assertion.
    """
    patterns = pattern_responses or {}
    call_log: list[dict[str, str]] = []

    def caller(system: str, user: str) -> str:
        call_log.append({"system": system, "user": user})
        for pat, resp in patterns.items():
            if re.search(pat, user, re.IGNORECASE):
                return resp
        return response

    caller.call_log = call_log  # type: ignore[attr-defined]
    return caller
