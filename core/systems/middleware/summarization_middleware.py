"""Context-compaction middleware backed by the canonical hygiene runtime."""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

try:
    from langchain.agents.middleware import AgentMiddleware
    from langchain.agents.middleware.types import ModelRequest, ModelResponse
    from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
    from langchain_core.tools import BaseTool, StructuredTool

    _HAS_LC = True
except ImportError:
    _HAS_LC = False
    AgentMiddleware = object  # type: ignore[assignment,misc]
    ModelRequest = object  # type: ignore[assignment,misc]
    ModelResponse = object  # type: ignore[assignment,misc]
    AIMessage = object  # type: ignore[assignment,misc]
    HumanMessage = object  # type: ignore[assignment,misc]
    ToolMessage = object  # type: ignore[assignment,misc]
    BaseTool = object  # type: ignore[assignment,misc]
    StructuredTool = None  # type: ignore[assignment,misc]

from core.systems.runtime.context_hygiene_runtime import ContextHygieneRuntime, count_message_tokens

from .agent_prompt_middleware import append_to_system_message

logger = logging.getLogger(__name__)

COMPACT_TOOL_PROMPT = """## Context Compaction - compact_conversation

You have a `compact_conversation` tool. Use it when:
- The user moves to a completely different topic
- You finished a large task and previous context is no longer needed
- The conversation feels long and previous working context is stale
"""


@dataclass
class SummarizationConfig:
    token_trigger: int = 100_000
    keep_recent_messages: int = 20
    mid_tier_messages: int = 14
    max_tool_arg_chars: int = 2000
    tool_arg_truncation_text: str = "...(truncated)"
    tool_arg_trigger_messages: int = 30
    offload_dir: str | None = None
    thread_id: str = "default"
    microcompact_age: int = 6


def _count_message_tokens(messages: list[Any]) -> int:
    return count_message_tokens(messages)


class SummarizationMiddleware(AgentMiddleware if _HAS_LC else object):  # type: ignore[misc]
    """Thin LangChain wrapper over ContextHygieneRuntime."""

    def __init__(
        self,
        *,
        summarize_fn: Callable[[str], str] | None = None,
        config: SummarizationConfig | None = None,
        compaction_callback: Callable[[dict[str, Any]], None] | None = None,
        session_memory_extractor: Any | None = None,
        runtime_view_provider: Callable[[], dict[str, Any] | None] | None = None,
        hooks_runtime: Any | None = None,
    ):
        self._summarize_fn = summarize_fn
        self._config = config or SummarizationConfig()
        self._compaction_callback = compaction_callback
        self._session_extractor = session_memory_extractor
        self._runtime_view_provider = runtime_view_provider
        self._seen_tool_call_keys: set[str] = set()
        self._runtime = ContextHygieneRuntime(config=self._config, hooks_runtime=hooks_runtime)
        self.tools: list[Any] = [self._build_compact_tool()]

    @property
    def name(self) -> str:
        return "SummarizationMiddleware"

    @property
    def _summary_message(self) -> HumanMessage | None:  # type: ignore[override]
        return self._runtime.summary_message

    @_summary_message.setter
    def _summary_message(self, value: HumanMessage | None) -> None:  # type: ignore[override]
        self._runtime.summary_message = value

    @property
    def _cutoff_index(self) -> int:  # type: ignore[override]
        return self._runtime.cutoff_index

    @_cutoff_index.setter
    def _cutoff_index(self, value: int) -> None:  # type: ignore[override]
        self._runtime.cutoff_index = int(value or 0)

    @property
    def _last_microcompact_count(self) -> int:  # type: ignore[override]
        return self._runtime.last_microcompact_count

    @_last_microcompact_count.setter
    def _last_microcompact_count(self, value: int) -> None:  # type: ignore[override]
        self._runtime.last_microcompact_count = int(value or 0)

    def get_context_hygiene_projection(self) -> dict[str, Any]:
        return self._runtime.build_projection()

    def _build_compact_tool(self) -> Any:
        middleware = self

        def compact_conversation() -> str:
            """Compact the conversation by summarizing older messages."""
            return middleware._run_compact()

        return StructuredTool.from_function(
            name="compact_conversation",
            description=(
                "Compact the conversation by summarizing older messages into a concise summary. "
                "Frees up context window space. Takes no arguments."
            ),
            func=compact_conversation,
        )

    def _run_compact(self) -> str:
        if not self._last_messages:
            return "Nothing to compact - conversation is short."
        effective = self._prepare_effective_messages(self._last_messages)
        if len(effective) <= self._config.keep_recent_messages:
            return "Nothing to compact - conversation is within budget."
        if self._session_extractor is not None:
            try:
                self._session_extractor.force_extract(effective, _count_message_tokens(effective))
            except Exception as exc:
                logger.debug("Session memory force_extract failed: %s", exc)
        result = self._do_summarize(effective)
        if result:
            return f"Compacted {result} messages into a summary."
        return "Compaction failed or was not needed."

    def wrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], ModelResponse],
    ) -> ModelResponse:
        messages = list(request.messages)
        self._last_messages = messages
        effective = self._prepare_effective_messages(messages)
        total_tokens = _count_message_tokens(effective)
        self._tick_session_extractor(messages, effective, total_tokens)
        if total_tokens >= self._config.token_trigger:
            count = self._do_summarize(effective)
            if count:
                effective = self._prepare_effective_messages(messages)
        request = request.override(
            messages=effective,
            system_message=append_to_system_message(request.system_message, COMPACT_TOOL_PROMPT),
        )
        return handler(request)

    async def awrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], Awaitable[ModelResponse]],
    ) -> ModelResponse:
        messages = list(request.messages)
        self._last_messages = messages
        effective = self._prepare_effective_messages(messages)
        total_tokens = _count_message_tokens(effective)
        self._tick_session_extractor(messages, effective, total_tokens)
        if total_tokens >= self._config.token_trigger:
            count = self._do_summarize(effective)
            if count:
                effective = self._prepare_effective_messages(messages)
        request = request.override(
            messages=effective,
            system_message=append_to_system_message(request.system_message, COMPACT_TOOL_PROMPT),
        )
        return await handler(request)

    def _prepare_effective_messages(self, messages: list[Any]) -> list[Any]:
        return self._runtime.prepare_effective_messages(messages)

    def _tick_session_extractor(
        self,
        messages: list[Any],
        effective: list[Any],
        total_tokens: int,
    ) -> None:
        if self._session_extractor is None:
            return
        delta = self._consume_new_tool_calls(messages)
        try:
            self._session_extractor.tick(
                effective,
                tool_call_delta=delta,
                current_token_count=total_tokens,
            )
        except Exception as exc:
            logger.debug("Session memory tick failed: %s", exc)

    def _consume_new_tool_calls(self, messages: list[Any]) -> int:
        delta = 0
        anonymous_counts: dict[str, int] = {}
        for message in messages:
            if not isinstance(message, AIMessage):
                continue
            for tool_call in getattr(message, "tool_calls", None) or []:
                call_id = tool_call.get("id")
                if call_id:
                    key = f"id:{call_id}"
                else:
                    signature = f"{tool_call.get('name', '')}:{repr(tool_call.get('args', {}))}"
                    occurrence = anonymous_counts.get(signature, 0)
                    anonymous_counts[signature] = occurrence + 1
                    key = f"sig:{signature}#{occurrence}"
                if key in self._seen_tool_call_keys:
                    continue
                self._seen_tool_call_keys.add(key)
                delta += 1
        return delta

    def _get_effective_messages(self, messages: list[Any]) -> list[Any]:
        return self._runtime.get_effective_messages(messages)

    def _microcompact(self, messages: list[Any]) -> list[Any]:
        return self._runtime.microcompact(messages)

    def _truncate_tool_args(self, messages: list[Any]) -> list[Any]:
        return self._runtime.truncate_tool_args(messages)

    def _do_summarize(self, effective: list[Any]) -> int | None:
        return self._runtime.summarize(
            effective,
            summarize_fn=self._summarize_fn,
            session_notes=self._get_session_notes() or "",
            resume_bundle_text=self._get_resume_bundle_text() or "",
            compaction_callback=self._compaction_callback,
            projected_runtime_view=self._get_resume_runtime_view() or {},
        )

    def _get_session_notes(self) -> str | None:
        if self._session_extractor is None:
            return None
        try:
            return self._session_extractor.get_notes()
        except Exception:
            return None

    def _get_resume_bundle_text(self) -> str | None:
        runtime_view = self._get_resume_runtime_view()
        if not runtime_view:
            return None
        try:
            from core.systems.runtime.session.session_runtime_view import render_runtime_view_context

            text = render_runtime_view_context(runtime_view)
            return text.strip() or None
        except Exception as exc:
            logger.debug("Resume artifacts render failed: %s", exc)
            return None

    def _get_resume_runtime_view(self) -> dict[str, Any] | None:
        if self._runtime_view_provider is None:
            return None
        try:
            runtime_view = self._runtime_view_provider()
            return runtime_view if isinstance(runtime_view, dict) and runtime_view else None
        except Exception as exc:
            logger.debug("Resume runtime view fetch failed: %s", exc)
            return None

    def _generate_episode_summaries(self, messages: list[Any]) -> list[str]:
        return self._runtime.generate_episode_summaries(messages, summarize_fn=self._summarize_fn)

    def _generate_summary(self, messages: list[Any]) -> str | None:
        return self._runtime.generate_summary(messages, summarize_fn=self._summarize_fn)

    def _offload(self, messages: list[Any]) -> None:
        self._runtime.offload(
            messages,
            projected_runtime_view=self._get_resume_runtime_view() or {},
        )

    @staticmethod
    def _is_summary_msg(message: Any) -> bool:
        return ContextHygieneRuntime.is_summary_msg(message)

    @staticmethod
    def _make_microcompact_stub(tool_label: str, content: str) -> str:
        return ContextHygieneRuntime.make_microcompact_stub(tool_label, content)

    @staticmethod
    def _format_tool_call_preview(tool_name: str, args: Any) -> str:
        return ContextHygieneRuntime.format_tool_call_preview(tool_name, args)

    def _tool_call_preview(self, messages: list[Any], tool_index: int, tool_message: ToolMessage) -> str:
        return self._runtime.tool_call_preview(messages, tool_index, tool_message)

    _last_messages: list[Any] = []
