"""LLM cost and usage tracking.

Implements a LangChain callback handler that records token usage,
model calls, and tool invocations for cost analysis.
"""

from __future__ import annotations

import json
import logging
import os
import time
from collections import defaultdict
from dataclasses import asdict, dataclass, field
from typing import Any

from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.outputs import LLMResult

logger = logging.getLogger(__name__)

_COST_PER_1K_TOKENS: dict[str, dict[str, float]] = {
    "gpt-4o": {"input": 0.0025, "output": 0.01},
    "gpt-4o-mini": {"input": 0.00015, "output": 0.0006},
    "gpt-4-turbo": {"input": 0.01, "output": 0.03},
    "gpt-4": {"input": 0.03, "output": 0.06},
    "gpt-3.5-turbo": {"input": 0.0005, "output": 0.0015},
    "claude-sonnet-4-20250514": {"input": 0.003, "output": 0.015},
    "claude-3-5-haiku-20241022": {"input": 0.001, "output": 0.005},
    "claude-3-opus-20240229": {"input": 0.015, "output": 0.075},
    "gemini-2.0-flash": {"input": 0.0001, "output": 0.0004},
    "gemini-1.5-pro": {"input": 0.00125, "output": 0.005},
}


@dataclass
class LLMCallRecord:
    """Single LLM invocation record."""

    model: str
    input_tokens: int
    output_tokens: int
    total_tokens: int
    cost_usd: float
    duration_ms: float
    timestamp: float


@dataclass
class ToolCallRecord:
    """Single tool invocation record."""

    tool_name: str
    duration_ms: float
    success: bool
    timestamp: float


@dataclass
class CostSummary:
    """Aggregated cost and usage summary."""

    total_llm_calls: int = 0
    total_tool_calls: int = 0
    total_input_tokens: int = 0
    total_output_tokens: int = 0
    total_tokens: int = 0
    total_cost_usd: float = 0.0
    total_llm_duration_ms: float = 0.0
    total_tool_duration_ms: float = 0.0
    model_breakdown: dict[str, dict[str, Any]] = field(default_factory=dict)
    tool_breakdown: dict[str, dict[str, Any]] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _estimate_cost(model: str, input_tokens: int, output_tokens: int) -> float:
    """Estimate USD cost based on known pricing."""
    model_lower = model.lower()
    for known_model, rates in _COST_PER_1K_TOKENS.items():
        if known_model in model_lower:
            return (input_tokens / 1000 * rates["input"]) + (output_tokens / 1000 * rates["output"])
    return 0.0


class CostTracker:
    """Tracks LLM and tool usage costs across sessions."""

    def __init__(self, persist_path: str | None = None):
        self._llm_calls: list[LLMCallRecord] = []
        self._tool_calls: list[ToolCallRecord] = []
        self._persist_path = persist_path

    def record_llm_call(
        self,
        model: str,
        input_tokens: int,
        output_tokens: int,
        duration_ms: float = 0.0,
    ) -> LLMCallRecord:
        total = input_tokens + output_tokens
        cost = _estimate_cost(model, input_tokens, output_tokens)
        record = LLMCallRecord(
            model=model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            total_tokens=total,
            cost_usd=cost,
            duration_ms=duration_ms,
            timestamp=time.time(),
        )
        self._llm_calls.append(record)
        self._persist()
        return record

    def record_tool_call(
        self,
        tool_name: str,
        duration_ms: float = 0.0,
        success: bool = True,
    ) -> ToolCallRecord:
        record = ToolCallRecord(
            tool_name=tool_name,
            duration_ms=duration_ms,
            success=success,
            timestamp=time.time(),
        )
        self._tool_calls.append(record)
        self._persist()
        return record

    def get_summary(self) -> CostSummary:
        """Get aggregated usage summary."""
        summary = CostSummary()
        model_data: dict[str, dict[str, Any]] = defaultdict(
            lambda: {"calls": 0, "input_tokens": 0, "output_tokens": 0, "cost_usd": 0.0}
        )
        tool_data: dict[str, dict[str, Any]] = defaultdict(
            lambda: {"calls": 0, "duration_ms": 0.0, "successes": 0, "failures": 0}
        )

        for r in self._llm_calls:
            summary.total_llm_calls += 1
            summary.total_input_tokens += r.input_tokens
            summary.total_output_tokens += r.output_tokens
            summary.total_tokens += r.total_tokens
            summary.total_cost_usd += r.cost_usd
            summary.total_llm_duration_ms += r.duration_ms
            model_data[r.model]["calls"] += 1
            model_data[r.model]["input_tokens"] += r.input_tokens
            model_data[r.model]["output_tokens"] += r.output_tokens
            model_data[r.model]["cost_usd"] += r.cost_usd

        for r in self._tool_calls:
            summary.total_tool_calls += 1
            summary.total_tool_duration_ms += r.duration_ms
            tool_data[r.tool_name]["calls"] += 1
            tool_data[r.tool_name]["duration_ms"] += r.duration_ms
            if r.success:
                tool_data[r.tool_name]["successes"] += 1
            else:
                tool_data[r.tool_name]["failures"] += 1

        summary.model_breakdown = dict(model_data)
        summary.tool_breakdown = dict(tool_data)
        return summary

    def _persist(self) -> None:
        if not self._persist_path:
            return
        try:
            os.makedirs(os.path.dirname(self._persist_path), exist_ok=True)
            data = {
                "llm_calls": [asdict(r) for r in self._llm_calls[-100:]],
                "tool_calls": [asdict(r) for r in self._tool_calls[-200:]],
            }
            with open(self._persist_path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except Exception as exc:
            logger.debug("Cost tracker persist failed: %s", exc)


class CostTrackerCallback(BaseCallbackHandler):
    """LangChain callback handler that feeds data into CostTracker."""

    def __init__(self, tracker: CostTracker):
        super().__init__()
        self.tracker = tracker
        self._llm_start_times: dict[str, float] = {}
        self._tool_start_times: dict[str, float] = {}

    def on_llm_start(self, serialized: dict, prompts: list[str], *, run_id: Any, **kwargs) -> None:
        self._llm_start_times[str(run_id)] = time.time()

    def on_llm_end(self, response: LLMResult, *, run_id: Any, **kwargs) -> None:
        duration_ms = 0.0
        start = self._llm_start_times.pop(str(run_id), None)
        if start:
            duration_ms = (time.time() - start) * 1000

        usage = (response.llm_output or {}).get("token_usage", {})
        input_tokens = usage.get("prompt_tokens", 0)
        output_tokens = usage.get("completion_tokens", 0)
        model = (response.llm_output or {}).get("model_name", "unknown")

        if input_tokens or output_tokens:
            self.tracker.record_llm_call(
                model=model,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                duration_ms=duration_ms,
            )

    def on_tool_start(self, serialized: dict, input_str: str, *, run_id: Any, **kwargs) -> None:
        self._tool_start_times[str(run_id)] = time.time()

    def on_tool_end(self, output: str, *, run_id: Any, **kwargs) -> None:
        duration_ms = 0.0
        start = self._tool_start_times.pop(str(run_id), None)
        if start:
            duration_ms = (time.time() - start) * 1000

        tool_name = kwargs.get("name", "unknown")
        self.tracker.record_tool_call(tool_name=tool_name, duration_ms=duration_ms, success=True)

    def on_tool_error(self, error: BaseException, *, run_id: Any, **kwargs) -> None:
        duration_ms = 0.0
        start = self._tool_start_times.pop(str(run_id), None)
        if start:
            duration_ms = (time.time() - start) * 1000

        tool_name = kwargs.get("name", "unknown")
        self.tracker.record_tool_call(tool_name=tool_name, duration_ms=duration_ms, success=False)
