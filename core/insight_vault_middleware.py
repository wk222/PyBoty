"""Task-level experience pool — learn from successful runs.

When the agent successfully completes a complex task, the middleware:
1. Extracts the decision trace (task → tool chain → outcome)
2. Distills it via LLM into concise, actionable advice
3. Stores the distilled experience in a vector-store collection

On new tasks it retrieves the top-K most similar past experiences and
injects them as few-shot examples in the system prompt, improving
first-try success rate and reducing token waste from blind exploration.
"""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

try:
    from langchain.agents.middleware import AgentMiddleware
    from langchain.agents.middleware.types import ModelRequest, ModelResponse
    from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

    _HAS_LC = True
except ImportError:
    _HAS_LC = False
    AgentMiddleware = object  # type: ignore[assignment,misc]
    ModelRequest = object  # type: ignore[assignment,misc]
    ModelResponse = object  # type: ignore[assignment,misc]

from .agent_prompt_middleware import append_to_system_message

logger = logging.getLogger(__name__)

COLLECTION_NAME = "insight_vault"

DISTILL_PROMPT = (
    "Distill this task execution into a concise experience record (3-6 sentences). "
    "Focus on: (1) what the task was, (2) the key strategy/approach that worked, "
    "(3) any pitfalls encountered and how they were resolved, (4) reusable patterns. "
    "Write it as advice for a future agent facing a similar task.\n\n{text}"
)


@dataclass
class InsightVaultConfig:
    top_k: int = 3
    min_tool_calls_to_record: int = 3
    max_experience_tokens: int = 600
    similarity_threshold: float = 0.3
    enable_llm_distill: bool = True
    max_raw_chars_for_distill: int = 4000


@dataclass
class TaskTrace:
    """Serializable record of a completed task."""

    task_description: str
    tool_chain: list[str]
    key_decisions: list[str]
    outcome: str
    timestamp: float = field(default_factory=time.time)

    def to_document_text(self) -> str:
        chain = " → ".join(self.tool_chain[:10])
        decisions = "\n".join(f"  - {d}" for d in self.key_decisions[:5])
        return (
            f"Task: {self.task_description}\n"
            f"Tool chain: {chain}\n"
            f"Key decisions:\n{decisions}\n"
            f"Outcome: {self.outcome}"
        )

    def to_metadata(self) -> dict[str, Any]:
        return {
            "task": self.task_description[:200],
            "tool_count": len(self.tool_chain),
            "timestamp": self.timestamp,
        }


class InsightVaultMiddleware(AgentMiddleware if _HAS_LC else object):  # type: ignore[misc]
    """Inject relevant past experiences into prompts and record new ones."""

    def __init__(
        self,
        *,
        vector_store: Any | None = None,
        config: InsightVaultConfig | None = None,
        distill_fn: Callable[[str], str] | None = None,
    ):
        self._vs = vector_store
        self._config = config or InsightVaultConfig()
        self._distill_fn = distill_fn
        self._current_task: str = ""
        self._tool_trace: list[dict[str, str]] = []
        self._injected_context: str = ""

    @property
    def name(self) -> str:
        return "InsightVaultMiddleware"

    def record_completed_task(
        self,
        task_description: str,
        messages: list[Any],
        outcome: str = "success",
    ) -> bool:
        """Extract, distill via LLM, and store a task experience."""
        if not self._vs:
            return False

        tool_chain, decisions = self._extract_trace(messages)
        if len(tool_chain) < self._config.min_tool_calls_to_record:
            return False

        trace = TaskTrace(
            task_description=task_description,
            tool_chain=tool_chain,
            key_decisions=decisions,
            outcome=outcome,
        )

        raw_text = trace.to_document_text()
        stored_text = self._distill_experience(raw_text) or raw_text

        from .vector_store import Document

        doc = Document(
            page_content=stored_text,
            metadata={**trace.to_metadata(), "distilled": stored_text != raw_text},
        )
        try:
            self._vs.add_documents([doc], collection=COLLECTION_NAME)
            logger.info("InsightVault: recorded experience for '%s' (distilled=%s)",
                        task_description[:60], stored_text != raw_text)
            return True
        except Exception as exc:
            logger.warning("InsightVault: failed to store experience: %s", exc)
            return False

    def _distill_experience(self, raw_text: str) -> str | None:
        """Use LLM to distill raw trace into a concise, actionable experience."""
        if not self._config.enable_llm_distill or not self._distill_fn:
            return None
        try:
            truncated = raw_text[:self._config.max_raw_chars_for_distill]
            return self._distill_fn(DISTILL_PROMPT.format(text=truncated)).strip()
        except Exception as exc:
            logger.warning("InsightVault: LLM distillation failed, storing raw: %s", exc)
            return None

    def _extract_trace(self, messages: list[Any]) -> tuple[list[str], list[str]]:
        """Extract tool chain and key decisions from message history."""
        tool_chain: list[str] = []
        decisions: list[str] = []

        for msg in messages:
            if isinstance(msg, AIMessage):
                for tc in getattr(msg, "tool_calls", None) or []:
                    name = tc.get("name", "unknown")
                    tool_chain.append(name)
                    args = tc.get("args", {})
                    args_summary = ", ".join(
                        f"{k}={str(v)[:30]}" for k, v in list(args.items())[:3]
                    )
                    decisions.append(f"called {name}({args_summary})")
            elif isinstance(msg, HumanMessage):
                content = getattr(msg, "content", "")
                if isinstance(content, str) and len(content) > 10:
                    if not self._current_task:
                        self._current_task = content[:200]

        return tool_chain, decisions[:8]

    def _retrieve_relevant(self, task: str) -> str:
        """Retrieve past experiences relevant to the current task."""
        if not self._vs or not task:
            return ""
        try:
            results = self._vs.search(
                query=task,
                collection=COLLECTION_NAME,
                top_k=self._config.top_k,
            )
        except Exception as exc:
            logger.debug("InsightVault: retrieval failed: %s", exc)
            return ""

        relevant = [
            r for r in results
            if r.score >= self._config.similarity_threshold
        ]
        if not relevant:
            return ""

        lines = ["## Past Experience Reference (InsightVault)", ""]
        for i, r in enumerate(relevant, 1):
            text = r.document.page_content[:self._config.max_experience_tokens]
            lines.append(f"### Experience #{i} (relevance: {r.score:.2f})")
            lines.append(text)
            lines.append("")

        lines.append(
            "Use these past experiences as reference but adapt to the current "
            "context. Do not blindly copy — evaluate what applies."
        )
        return "\n".join(lines)

    def _extract_current_task(self, messages: list[Any]) -> str:
        """Find the most recent user message as current task description."""
        for msg in reversed(messages):
            if isinstance(msg, HumanMessage):
                content = getattr(msg, "content", "")
                if isinstance(content, str) and len(content) > 5:
                    kw = msg.additional_kwargs or {}
                    if kw.get("lc_source") == "summarization":
                        continue
                    return content[:300]
        return ""

    def _process_request(self, request: Any) -> Any:
        messages = list(request.messages)
        task = self._extract_current_task(messages)
        if not task:
            return request

        context = self._retrieve_relevant(task)
        if not context:
            return request

        self._injected_context = context
        return request.override(
            system_message=append_to_system_message(request.system_message, context),
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
        stats: dict[str, Any] = {
            "has_vector_store": self._vs is not None,
            "current_task_tracked": bool(self._current_task),
            "last_injected_length": len(self._injected_context),
        }
        if self._vs:
            try:
                count_results = self._vs.search("", collection=COLLECTION_NAME, top_k=1)
                stats["stored_experiences"] = "available"
            except Exception:
                stats["stored_experiences"] = "unknown"
        return stats
