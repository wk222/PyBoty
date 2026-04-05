"""Pluggable context engine — assemble / compact / lifecycle hooks.

Provides a standard
interface for managing the context window that is sent to the LLM:

  * **assemble** — build the final list of messages (system prompt,
    memories, conversation history, tool results) within a token budget.
  * **compact** — compress or trim the message list when the context
    window overflows (e.g. summarise older turns, drop low-value messages).
  * **after_turn** — post-processing hook called after each model turn
    (persist state, trigger compaction if needed, emit diagnostics).

A global **registry** allows multiple engine implementations to coexist
and be selected via configuration.

Typical usage::

    from core.systems.context.context_engine import get_engine, register_engine

    register_engine(MyCustomEngine())

    engine = get_engine("my_engine")
    result = engine.assemble(messages, token_budget=4096)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

logger = logging.getLogger(__name__)


@dataclass
class AssembleResult:
    """Output of ``ContextEngine.assemble``."""
    messages: list[Any]
    estimated_tokens: int = 0
    system_prompt_addition: str = ""
    dropped_count: int = 0


@dataclass
class CompactResult:
    """Output of ``ContextEngine.compact``."""
    ok: bool = True
    messages: list[Any] = field(default_factory=list)
    summary: str = ""
    tokens_before: int = 0
    tokens_after: int = 0


@runtime_checkable
class ContextEngine(Protocol):
    """Interface every context engine must satisfy."""

    @property
    def engine_id(self) -> str: ...

    def assemble(
        self,
        messages: list[Any],
        *,
        token_budget: int = 4096,
        system_prompt: str = "",
        memory_context: str = "",
    ) -> AssembleResult:
        """Build the final message list within *token_budget*."""
        ...

    def compact(
        self,
        messages: list[Any],
        *,
        target_tokens: int = 2048,
    ) -> CompactResult:
        """Compress or trim *messages* to fit *target_tokens*."""
        ...

    def after_turn(self, messages: list[Any], response: Any) -> None:
        """Post-turn hook (persist state, trigger compaction, etc.)."""
        ...


_ENGINE_REGISTRY: dict[str, ContextEngine] = {}


def register_engine(engine: ContextEngine) -> None:
    _ENGINE_REGISTRY[engine.engine_id] = engine
    logger.info("Registered context engine: %s", engine.engine_id)


def unregister_engine(engine_id: str) -> bool:
    return _ENGINE_REGISTRY.pop(engine_id, None) is not None


def get_engine(engine_id: str) -> ContextEngine | None:
    return _ENGINE_REGISTRY.get(engine_id)


def list_engines() -> list[str]:
    return list(_ENGINE_REGISTRY.keys())


def _estimate_tokens(text: str) -> int:
    """Rough token estimate: ~4 chars per token for English, ~2 for CJK."""
    cjk = sum(1 for c in text if "\u4e00" <= c <= "\u9fff")
    ascii_chars = len(text) - cjk
    return int(ascii_chars / 4 + cjk / 2)


def _message_text(msg: Any) -> str:
    if isinstance(msg, dict):
        return str(msg.get("content", ""))
    return str(getattr(msg, "content", ""))


def _message_role(msg: Any) -> str:
    if isinstance(msg, dict):
        return msg.get("role", "unknown")
    t = getattr(msg, "type", "unknown")
    return {"human": "user", "ai": "assistant", "system": "system"}.get(t, t)


class DefaultContextEngine:
    """Built-in context engine with simple token-budget trimming.

    Strategy:
      1. System prompt + memory context always included.
      2. Most recent messages kept; older messages trimmed from the middle.
      3. Compact via summarisation (if LLM available) or hard truncation.
    """

    def __init__(self, *, llm: Any = None, keep_recent: int = 10):
        self._llm = llm
        self._keep_recent = keep_recent

    @property
    def engine_id(self) -> str:
        return "default"

    def assemble(
        self,
        messages: list[Any],
        *,
        token_budget: int = 4096,
        system_prompt: str = "",
        memory_context: str = "",
    ) -> AssembleResult:
        result_messages: list[Any] = []
        system_addition = ""

        if system_prompt:
            full_system = system_prompt
            if memory_context:
                full_system += f"\n\n{memory_context}"
                system_addition = memory_context
            result_messages.append({"role": "system", "content": full_system})
        elif memory_context:
            result_messages.append({"role": "system", "content": memory_context})
            system_addition = memory_context

        budget_used = sum(_estimate_tokens(_message_text(m)) for m in result_messages)

        recent = messages[-self._keep_recent:] if len(messages) > self._keep_recent else messages
        dropped = len(messages) - len(recent)

        for msg in recent:
            tokens = _estimate_tokens(_message_text(msg))
            if budget_used + tokens > token_budget and len(result_messages) > 1:
                dropped += 1
                continue
            result_messages.append(msg)
            budget_used += tokens

        return AssembleResult(
            messages=result_messages,
            estimated_tokens=budget_used,
            system_prompt_addition=system_addition,
            dropped_count=dropped,
        )

    def compact(
        self,
        messages: list[Any],
        *,
        target_tokens: int = 2048,
    ) -> CompactResult:
        tokens_before = sum(_estimate_tokens(_message_text(m)) for m in messages)
        if tokens_before <= target_tokens:
            return CompactResult(ok=True, messages=list(messages), tokens_before=tokens_before, tokens_after=tokens_before)

        if self._llm is not None:
            return self._compact_with_summary(messages, target_tokens, tokens_before)

        return self._compact_by_truncation(messages, target_tokens, tokens_before)

    def _compact_with_summary(
        self, messages: list[Any], target_tokens: int, tokens_before: int
    ) -> CompactResult:
        """Summarise older messages to fit the budget."""
        system_msgs = [m for m in messages if _message_role(m) == "system"]
        non_system = [m for m in messages if _message_role(m) != "system"]

        keep_count = max(2, len(non_system) // 3)
        to_summarise = non_system[:-keep_count] if len(non_system) > keep_count else []
        to_keep = non_system[-keep_count:]

        if not to_summarise:
            return self._compact_by_truncation(messages, target_tokens, tokens_before)

        summary_text = "\n".join(
            f"{_message_role(m)}: {_message_text(m)[:200]}" for m in to_summarise
        )
        try:
            prompt = f"Summarise the following conversation in under 200 words:\n\n{summary_text}"
            response = self._llm.invoke(prompt)
            summary = response.content if hasattr(response, "content") else str(response)
        except Exception as exc:
            logger.debug("Compact summarisation failed: %s", exc)
            return self._compact_by_truncation(messages, target_tokens, tokens_before)

        result_messages = list(system_msgs)
        result_messages.append({"role": "system", "content": f"[Earlier conversation summary]\n{summary}"})
        result_messages.extend(to_keep)

        tokens_after = sum(_estimate_tokens(_message_text(m)) for m in result_messages)
        return CompactResult(
            ok=True,
            messages=result_messages,
            summary=summary,
            tokens_before=tokens_before,
            tokens_after=tokens_after,
        )

    def _compact_by_truncation(
        self, messages: list[Any], target_tokens: int, tokens_before: int
    ) -> CompactResult:
        """Hard-drop oldest non-system messages until we fit."""
        system_msgs = [m for m in messages if _message_role(m) == "system"]
        non_system = [m for m in messages if _message_role(m) != "system"]

        kept: list[Any] = list(system_msgs)
        current_tokens = sum(_estimate_tokens(_message_text(m)) for m in kept)

        for msg in reversed(non_system):
            t = _estimate_tokens(_message_text(msg))
            if current_tokens + t <= target_tokens:
                kept.insert(len(system_msgs), msg)
                current_tokens += t

        return CompactResult(
            ok=True,
            messages=kept,
            tokens_before=tokens_before,
            tokens_after=current_tokens,
        )

    def after_turn(self, messages: list[Any], response: Any) -> None:
        total_tokens = sum(_estimate_tokens(_message_text(m)) for m in messages)
        logger.debug("After turn: %d messages, ~%d tokens", len(messages), total_tokens)
