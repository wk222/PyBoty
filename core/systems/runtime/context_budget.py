"""Context budget manager for session token pressure tracking and compaction decisions.

Tracks rolling token usage against model context limits and provides actionable
pressure assessments: micro-trim expensive tool outputs, compact history, or
rehydrate from session memory instead of replaying raw history.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any


_DEFAULT_MODEL_LIMITS: dict[str, int] = {
    "gpt-4o": 128_000,
    "gpt-4o-mini": 128_000,
    "gpt-4-turbo": 128_000,
    "gpt-4": 8_192,
    "claude-3-5-sonnet": 200_000,
    "claude-3-5-haiku": 200_000,
    "claude-sonnet-4": 200_000,
    "claude-opus-4": 200_000,
    "gemini-2.0-flash": 1_000_000,
    "gemini-1.5-pro": 2_000_000,
}

_DEFAULT_LIMIT = 128_000

PRESSURE_LOW = "low"
PRESSURE_MODERATE = "moderate"
PRESSURE_HIGH = "high"
PRESSURE_CRITICAL = "critical"

_TRIM_CHARS_BY_PRESSURE: dict[str, int] = {
    PRESSURE_LOW: 4000,
    PRESSURE_MODERATE: 1200,
    PRESSURE_HIGH: 500,
    PRESSURE_CRITICAL: 200,
}


@dataclass(frozen=True)
class BudgetAssessment:
    """Snapshot of current token pressure with recommended actions."""

    level: str
    estimated_tokens: int
    context_limit: int
    utilization: float
    should_micro_trim: bool
    should_compact: bool
    should_rehydrate: bool
    trim_targets: list[str]
    notes: list[str]
    assessed_at: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        return {
            "level": self.level,
            "estimated_tokens": self.estimated_tokens,
            "context_limit": self.context_limit,
            "utilization": round(self.utilization, 4),
            "should_micro_trim": self.should_micro_trim,
            "should_compact": self.should_compact,
            "should_rehydrate": self.should_rehydrate,
            "trim_targets": list(self.trim_targets),
            "notes": list(self.notes),
            "assessed_at": self.assessed_at,
        }


@dataclass
class TokenUsageRecord:
    input_tokens: int
    output_tokens: int
    recorded_at: float = field(default_factory=time.time)
    run_kind: str = "chat"
    label: str = ""
    tool_name: str = ""
    tool_output_chars: int = 0


class ContextBudgetManager:
    """Tracks rolling token usage and advises on context pressure actions.

    Thresholds:
      - moderate  (trim_threshold):   recommend micro-trimming expensive outputs
      - high      (compact_threshold): recommend history compaction
      - critical  (rehydrate_threshold): recommend rehydrating from session memory
    """

    def __init__(
        self,
        *,
        model_name: str = "",
        context_limit: int = 0,
        trim_threshold: float = 0.65,
        compact_threshold: float = 0.80,
        rehydrate_threshold: float = 0.90,
        max_usage_history: int = 64,
    ) -> None:
        self._model_name = model_name.strip().lower()
        self._context_limit = context_limit or self._resolve_limit(self._model_name)
        self._trim_threshold = trim_threshold
        self._compact_threshold = compact_threshold
        self._rehydrate_threshold = rehydrate_threshold
        self._max_usage_history = max_usage_history
        self._usage_history: list[TokenUsageRecord] = []
        self._cumulative_input = 0
        self._cumulative_output = 0
        self._tool_output_totals: dict[str, int] = {}

    @staticmethod
    def _resolve_limit(model_name: str) -> int:
        for key, limit in _DEFAULT_MODEL_LIMITS.items():
            if key in model_name:
                return limit
        return _DEFAULT_LIMIT

    @property
    def context_limit(self) -> int:
        return self._context_limit

    def record_usage(
        self,
        input_tokens: int,
        output_tokens: int,
        *,
        run_kind: str = "chat",
        label: str = "",
        tool_name: str = "",
        tool_output_chars: int = 0,
    ) -> None:
        record = TokenUsageRecord(
            input_tokens=max(0, int(input_tokens)),
            output_tokens=max(0, int(output_tokens)),
            run_kind=run_kind,
            label=label,
            tool_name=tool_name,
            tool_output_chars=tool_output_chars,
        )
        self._usage_history.append(record)
        if len(self._usage_history) > self._max_usage_history:
            self._usage_history = self._usage_history[-self._max_usage_history :]
        self._cumulative_input += record.input_tokens
        self._cumulative_output += record.output_tokens
        if tool_name and tool_output_chars:
            self._tool_output_totals[tool_name] = (
                self._tool_output_totals.get(tool_name, 0) + tool_output_chars
            )

    def record_tool_output(
        self,
        tool_name: str,
        output: str,
        *,
        label: str = "",
    ) -> None:
        """Record a tool output and accumulate its cost against the budget."""
        output_chars = len(str(output or ""))
        estimated_tokens = output_chars // 4
        self.record_usage(
            input_tokens=0,
            output_tokens=estimated_tokens,
            run_kind="tool",
            label=label or tool_name,
            tool_name=tool_name,
            tool_output_chars=output_chars,
        )

    def estimate_session_tokens(self, session_record: Any) -> int:
        """Rough token estimate from session state without calling LLM APIs."""
        tokens = 0
        if not session_record:
            return tokens

        if isinstance(session_record, dict):
            timeline = session_record.get("timeline", [])
            message_count = session_record.get("message_count", 0)
            working_summary = session_record.get("working_summary", "")
            context_notes = session_record.get("context_notes", [])
        else:
            timeline = getattr(session_record, "timeline", [])
            message_count = getattr(session_record, "message_count", 0)
            working_summary = getattr(session_record, "working_summary", "")
            context_notes = getattr(session_record, "context_notes", [])

        tokens += int(message_count) * 150
        tokens += len(str(working_summary)) // 4
        tokens += sum(len(str(note)) // 4 for note in context_notes)
        tokens += len(timeline) * 80

        if self._usage_history:
            recent = self._usage_history[-1]
            tokens = max(tokens, recent.input_tokens)

        return tokens

    def assess(self, session_record: Any = None) -> BudgetAssessment:
        estimated = self.estimate_session_tokens(session_record)
        utilization = estimated / self._context_limit if self._context_limit > 0 else 0.0

        trim_targets: list[str] = []
        notes: list[str] = []

        if utilization >= self._rehydrate_threshold:
            level = PRESSURE_CRITICAL
            should_compact = True
            should_micro_trim = True
            should_rehydrate = True
            trim_targets = ["tool_outputs", "file_views", "timeline_events", "context_notes"]
            notes.append(f"Critical utilization {utilization:.0%} — rehydrate from session memory")
        elif utilization >= self._compact_threshold:
            level = PRESSURE_HIGH
            should_compact = True
            should_micro_trim = True
            should_rehydrate = False
            trim_targets = ["tool_outputs", "file_views"]
            notes.append(f"High utilization {utilization:.0%} — compact history")
        elif utilization >= self._trim_threshold:
            level = PRESSURE_MODERATE
            should_compact = False
            should_micro_trim = True
            should_rehydrate = False
            trim_targets = ["tool_outputs"]
            notes.append(f"Moderate utilization {utilization:.0%} — trim expensive outputs")
        else:
            level = PRESSURE_LOW
            should_compact = False
            should_micro_trim = False
            should_rehydrate = False

        return BudgetAssessment(
            level=level,
            estimated_tokens=estimated,
            context_limit=self._context_limit,
            utilization=utilization,
            should_micro_trim=should_micro_trim,
            should_compact=should_compact,
            should_rehydrate=should_rehydrate,
            trim_targets=trim_targets,
            notes=notes,
        )

    def micro_trim_tool_output(
        self,
        output: str,
        *,
        pressure_level: str = PRESSURE_LOW,
        max_chars: int = 0,
    ) -> tuple[str, int]:
        """Trim a tool output to fit within a pressure-aware character budget.

        Returns (trimmed_text, chars_removed).
        """
        budget = max_chars or _TRIM_CHARS_BY_PRESSURE.get(pressure_level, 1200)
        text = str(output or "").strip()
        if len(text) <= budget:
            return text, 0
        head = text[: budget // 2]
        tail = text[-(budget // 4) :]
        removed = len(text) - budget
        return f"{head}\n… [{removed} chars trimmed] …\n{tail}", removed

    def apply_micro_trim(
        self,
        tool_outputs: dict[str, str],
        *,
        assessment: BudgetAssessment | None = None,
        session_record: Any = None,
    ) -> tuple[dict[str, str], dict[str, int]]:
        """Apply micro-trim rules to a map of tool outputs.

        Returns (trimmed_outputs, {tool_name: chars_removed}).
        Only trims if `assessment.should_micro_trim` is True.
        """
        if assessment is None:
            assessment = self.assess(session_record)

        if not assessment.should_micro_trim:
            return dict(tool_outputs), {}

        trimmed: dict[str, str] = {}
        removed: dict[str, int] = {}
        should_trim_tool = "tool_outputs" in assessment.trim_targets

        for name, output in tool_outputs.items():
            if should_trim_tool:
                new_text, chars = self.micro_trim_tool_output(
                    output, pressure_level=assessment.level
                )
                trimmed[name] = new_text
                if chars:
                    removed[name] = chars
            else:
                trimmed[name] = output

        return trimmed, removed

    def top_expensive_tools(self, *, top_n: int = 5) -> list[tuple[str, int]]:
        """Return the top N tools by total output character cost, descending."""
        return sorted(self._tool_output_totals.items(), key=lambda x: x[1], reverse=True)[:top_n]

    def usage_summary(self) -> dict[str, Any]:
        if not self._usage_history:
            return {"records": 0, "cumulative_input": 0, "cumulative_output": 0}
        recent = self._usage_history[-1]
        summary: dict[str, Any] = {
            "records": len(self._usage_history),
            "cumulative_input": self._cumulative_input,
            "cumulative_output": self._cumulative_output,
            "last_input": recent.input_tokens,
            "last_output": recent.output_tokens,
            "last_run_kind": recent.run_kind,
        }
        if self._tool_output_totals:
            summary["top_tools_by_output"] = self.top_expensive_tools()
        return summary
