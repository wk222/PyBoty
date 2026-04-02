"""LLM-driven conversation summarization middleware.

Inspired by DeepAgents' SummarizationMiddleware — upgrades PyBot's rule-based
ContextWindowManager with:

1. **LLM summarization** — older messages are summarized via a model call
2. **Model-aware triggers** — threshold calculated from token count
3. **Tool-arg truncation** — large write_file/edit_file args clipped before
   full summarization fires (reduces token waste)
4. **compact_conversation tool** — agent can proactively compact its context
5. **History offload** — evicted messages appended to per-thread markdown files
"""

from __future__ import annotations

import logging
import os
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

try:
    from langchain.agents.middleware import AgentMiddleware
    from langchain.agents.middleware.types import ModelRequest, ModelResponse
    from langchain_core.messages import (
        AIMessage,
        HumanMessage,
        get_buffer_string,
    )
    from langchain_core.tools import BaseTool, StructuredTool

    _HAS_LC = True
except ImportError:
    _HAS_LC = False
    AgentMiddleware = object  # type: ignore[assignment,misc]
    ModelRequest = object  # type: ignore[assignment,misc]
    ModelResponse = object  # type: ignore[assignment,misc]
    BaseTool = object  # type: ignore[assignment,misc]

from core.context_manager import count_tokens_approx

from .agent_prompt_middleware import append_to_system_message

logger = logging.getLogger(__name__)

SUMMARY_PROMPT = (
    "Summarize the following conversation in 3-8 sentences. "
    "Preserve key facts, decisions, action items, and any file "
    "paths or code snippets that are still relevant:\n\n{text}"
)

COMPACT_TOOL_PROMPT = """## Context Compaction — compact_conversation

You have a `compact_conversation` tool. Use it when:
- The user moves to a completely different topic
- You finished a large task and previous context is no longer needed
- The conversation feels long and previous working context is stale
"""

_TRUNCATABLE_TOOLS = frozenset({"write_file", "edit_file", "create_file"})


EPISODE_SUMMARY_PROMPT = (
    "Condense this tool interaction into one concise line. "
    "Include: tool name, key input, and outcome. Example: "
    "'write_file(main.py) → created 45-line Flask app'\n\n{text}"
)


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


def _count_message_tokens(messages: list[Any]) -> int:
    total = 0
    for msg in messages:
        content = getattr(msg, "content", "")
        if isinstance(content, str):
            total += count_tokens_approx(content)
        elif isinstance(content, list):
            for part in content:
                total += count_tokens_approx(str(part))
        for tc in getattr(msg, "tool_calls", []) or []:
            total += count_tokens_approx(str(tc.get("args", "")))
    return total


class SummarizationMiddleware(AgentMiddleware if _HAS_LC else object):  # type: ignore[misc]
    """LLM-driven summarization with tool-arg truncation and offload."""

    def __init__(
        self,
        *,
        summarize_fn: Callable[[str], str] | None = None,
        config: SummarizationConfig | None = None,
        compaction_callback: Callable[[dict[str, Any]], None] | None = None,
    ):
        self._summarize_fn = summarize_fn
        self._config = config or SummarizationConfig()
        self._compaction_callback = compaction_callback
        self._cutoff_index: int = 0
        self._summary_message: HumanMessage | None = None
        self.tools: list[Any] = [self._build_compact_tool()]

    @property
    def name(self) -> str:
        return "SummarizationMiddleware"

    def _build_compact_tool(self) -> Any:
        mw = self

        def compact_conversation() -> str:
            """Compact the conversation by summarizing older messages."""
            return mw._run_compact()

        return StructuredTool.from_function(
            name="compact_conversation",
            description=(
                "Compact the conversation by summarizing older messages "
                "into a concise summary. Frees up context window space. "
                "Takes no arguments."
            ),
            func=compact_conversation,
        )

    def _run_compact(self) -> str:
        if not self._last_messages:
            return "Nothing to compact — conversation is short."
        effective = self._get_effective_messages(self._last_messages)
        if len(effective) <= self._config.keep_recent_messages:
            return "Nothing to compact — conversation is within budget."
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
        effective = self._get_effective_messages(messages)
        effective = self._truncate_tool_args(effective)
        total_tokens = _count_message_tokens(effective)
        if total_tokens >= self._config.token_trigger:
            count = self._do_summarize(effective)
            if count:
                effective = self._get_effective_messages(messages)
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
        effective = self._get_effective_messages(messages)
        effective = self._truncate_tool_args(effective)
        total_tokens = _count_message_tokens(effective)
        if total_tokens >= self._config.token_trigger:
            count = self._do_summarize(effective)
            if count:
                effective = self._get_effective_messages(messages)
        request = request.override(
            messages=effective,
            system_message=append_to_system_message(request.system_message, COMPACT_TOOL_PROMPT),
        )
        return await handler(request)

    def _get_effective_messages(self, messages: list[Any]) -> list[Any]:
        if self._summary_message is None:
            return list(messages)
        result: list[Any] = [self._summary_message]
        if self._cutoff_index < len(messages):
            result.extend(messages[self._cutoff_index :])
        return result

    def _truncate_tool_args(self, messages: list[Any]) -> list[Any]:
        cfg = self._config
        if len(messages) < cfg.tool_arg_trigger_messages:
            return messages
        cutoff = max(0, len(messages) - cfg.keep_recent_messages)
        result = []
        for i, msg in enumerate(messages):
            if i < cutoff and isinstance(msg, AIMessage) and getattr(msg, "tool_calls", None):
                truncated_calls = []
                modified = False
                for tc in msg.tool_calls:
                    if tc.get("name") in _TRUNCATABLE_TOOLS:
                        new_args = {}
                        for k, v in tc.get("args", {}).items():
                            if isinstance(v, str) and len(v) > cfg.max_tool_arg_chars:
                                new_args[k] = v[:20] + cfg.tool_arg_truncation_text
                                modified = True
                            else:
                                new_args[k] = v
                        truncated_calls.append({**tc, "args": new_args})
                    else:
                        truncated_calls.append(tc)
                if modified:
                    new_msg = msg.model_copy()
                    new_msg.tool_calls = truncated_calls
                    result.append(new_msg)
                    continue
            result.append(msg)
        return result

    def _do_summarize(self, effective: list[Any]) -> int | None:
        keep = self._config.keep_recent_messages
        mid = self._config.mid_tier_messages
        if len(effective) <= keep:
            return None

        cutoff = len(effective) - keep
        to_summarize = effective[:cutoff]

        self._offload(to_summarize)

        if cutoff > mid:
            old_batch = to_summarize[: cutoff - mid]
            mid_batch = to_summarize[cutoff - mid:]
            bulk_summary = self._generate_summary(old_batch)
            episode_summaries = self._generate_episode_summaries(mid_batch)
            parts = []
            if bulk_summary:
                parts.append(f"### Earlier context\n{bulk_summary}")
            if episode_summaries:
                parts.append("### Recent actions\n" + "\n".join(f"- {s}" for s in episode_summaries))
            summary_text = "\n\n".join(parts) if parts else None
        else:
            episode_summaries = self._generate_episode_summaries(to_summarize)
            if episode_summaries:
                summary_text = "### Recent actions\n" + "\n".join(f"- {s}" for s in episode_summaries)
            else:
                summary_text = self._generate_summary(to_summarize)

        if not summary_text:
            return None

        file_hint = ""
        if self._config.offload_dir:
            file_hint = f"\n\nFull history saved to {self._config.offload_dir}/{self._config.thread_id}.md"

        self._summary_message = HumanMessage(
            content=(f"[Previous conversation summarized]{file_hint}\n\n<summary>\n{summary_text}\n</summary>"),
            additional_kwargs={"lc_source": "summarization"},
        )
        if self._compaction_callback is not None:
            try:
                self._compaction_callback(
                    {
                        "thread_id": self._config.thread_id,
                        "summary": summary_text,
                        "source": "middleware.summarization",
                        "reason": "conversation_compaction",
                        "message_count": len(to_summarize),
                        "recent_window": self._config.keep_recent_messages,
                        "offload_path": (
                            os.path.join(self._config.offload_dir, f"{self._config.thread_id}.md")
                            if self._config.offload_dir
                            else ""
                        ),
                    }
                )
            except Exception as exc:
                logger.warning("Compaction callback failed: %s", exc)
        old_cutoff = self._cutoff_index
        self._cutoff_index = old_cutoff + cutoff - (1 if self._summary_message else 0)
        return len(to_summarize)

    def _generate_episode_summaries(self, messages: list[Any]) -> list[str]:
        """Generate per-episode one-line summaries for mid-tier messages."""
        summaries: list[str] = []
        i = 0
        while i < len(messages):
            msg = messages[i]
            if isinstance(msg, AIMessage) and getattr(msg, "tool_calls", None):
                tc = msg.tool_calls[0]
                tool_name = tc.get("name", "unknown")
                args_preview = str(tc.get("args", {}))[:80]
                result_preview = ""
                if i + 1 < len(messages):
                    next_msg = messages[i + 1]
                    result_content = getattr(next_msg, "content", "")
                    if isinstance(result_content, str):
                        result_preview = result_content[:100]
                if self._summarize_fn:
                    try:
                        text = f"Tool: {tool_name}, Args: {args_preview}, Result: {result_preview}"
                        one_liner = self._summarize_fn(EPISODE_SUMMARY_PROMPT.format(text=text))
                        summaries.append(one_liner.strip())
                        i += 2
                        continue
                    except Exception:
                        pass
                summaries.append(f"{tool_name}({args_preview[:40]}) → {result_preview[:60]}")
                i += 2
                continue
            i += 1
        return summaries

    def _generate_summary(self, messages: list[Any]) -> str | None:
        if self._summarize_fn:
            try:
                text = get_buffer_string(messages)[:8000]
                return self._summarize_fn(SUMMARY_PROMPT.format(text=text))
            except Exception as exc:
                logger.warning("LLM summarization failed: %s", exc)

        topics = []
        for msg in messages:
            content = getattr(msg, "content", "")
            if isinstance(content, str) and len(content) > 5:
                role = getattr(msg, "type", "unknown")
                if role == "human":
                    topics.append(content[:120])
        if not topics:
            return None
        return "Topics discussed: " + "; ".join(topics[-8:])

    def _offload(self, messages: list[Any]) -> None:
        offload_dir = self._config.offload_dir
        if not offload_dir or not messages:
            return
        try:
            os.makedirs(offload_dir, exist_ok=True)
            path = os.path.join(offload_dir, f"{self._config.thread_id}.md")
            timestamp = datetime.now(UTC).isoformat()
            buf = get_buffer_string([m for m in messages if not self._is_summary_msg(m)])
            section = f"\n\n## Summarized at {timestamp}\n\n{buf}\n"
            with open(path, "a", encoding="utf-8") as f:
                f.write(section)
        except Exception as exc:
            logger.warning("Offload failed: %s", exc)

    @staticmethod
    def _is_summary_msg(msg: Any) -> bool:
        if not isinstance(msg, HumanMessage):
            return False
        return msg.additional_kwargs.get("lc_source") == "summarization"

    _last_messages: list[Any] = []
