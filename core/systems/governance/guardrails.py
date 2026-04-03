"""Output guardrails — automatic quality checks with retry.

Guardrails validate LLM/agent output against rules. When a check fails,
feedback is returned so the caller can retry with the feedback injected
into context.
"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

from pydantic import BaseModel

from core.systems.runtime.event_bus import Event, EventType, event_bus

logger = logging.getLogger(__name__)


@dataclass
class GuardrailResult:
    passed: bool
    feedback: str | None = None
    corrected_output: Any | None = None


@runtime_checkable
class Guardrail(Protocol):
    def check(self, output: str, context: dict[str, Any]) -> GuardrailResult: ...


class LengthGuardrail:
    """Check output length (character count)."""

    def __init__(self, min_len: int = 0, max_len: int = 100_000):
        self.min_len = min_len
        self.max_len = max_len

    def check(self, output: str, context: dict[str, Any]) -> GuardrailResult:
        n = len(output)
        if n < self.min_len:
            return GuardrailResult(
                passed=False,
                feedback=f"Output too short ({n} chars, minimum {self.min_len}). Please provide more detail.",
            )
        if n > self.max_len:
            return GuardrailResult(
                passed=False,
                feedback=f"Output too long ({n} chars, maximum {self.max_len}). Please be more concise.",
            )
        return GuardrailResult(passed=True)


class JsonGuardrail:
    """Check output is valid JSON and optionally conforms to a Pydantic schema."""

    def __init__(self, schema: type[BaseModel] | None = None):
        self.schema = schema

    def check(self, output: str, context: dict[str, Any]) -> GuardrailResult:
        try:
            data = json.loads(output)
        except (json.JSONDecodeError, TypeError) as exc:
            return GuardrailResult(
                passed=False,
                feedback=f"Output is not valid JSON: {exc}. Please return valid JSON.",
            )
        if self.schema is not None:
            try:
                self.schema.model_validate(data)
            except Exception as exc:
                return GuardrailResult(
                    passed=False,
                    feedback=f"JSON does not match schema {self.schema.__name__}: {exc}",
                )
        return GuardrailResult(passed=True)


class RegexGuardrail:
    """Check output matches (or doesn't match) a regex pattern."""

    def __init__(self, pattern: str, *, must_match: bool = True):
        self.pattern = pattern
        self.must_match = must_match
        self._compiled = re.compile(pattern, re.DOTALL)

    def check(self, output: str, context: dict[str, Any]) -> GuardrailResult:
        found = bool(self._compiled.search(output))
        if self.must_match and not found:
            return GuardrailResult(
                passed=False,
                feedback=f"Output must match pattern /{self.pattern}/ but didn't.",
            )
        if not self.must_match and found:
            return GuardrailResult(
                passed=False,
                feedback=f"Output must NOT match pattern /{self.pattern}/ but did.",
            )
        return GuardrailResult(passed=True)


class LLMGuardrail:
    """Use an LLM to judge output quality against an instruction."""

    def __init__(self, instruction: str, llm: Any):
        self.instruction = instruction
        self.llm = llm

    def check(self, output: str, context: dict[str, Any]) -> GuardrailResult:
        prompt = (
            f"Evaluate the following output against this criterion:\n"
            f"Criterion: {self.instruction}\n\n"
            f"Output:\n{output}\n\n"
            f'Respond with ONLY a JSON object: {{"pass": true/false, "feedback": "..."}}'
        )
        try:
            response = self.llm.invoke(prompt)
            text = response.content if hasattr(response, "content") else str(response)
            data = json.loads(text)
            if data.get("pass"):
                return GuardrailResult(passed=True)
            return GuardrailResult(
                passed=False,
                feedback=data.get("feedback", "LLM judge rejected the output."),
            )
        except Exception as exc:
            logger.warning("LLMGuardrail failed to parse LLM response: %s", exc)
            return GuardrailResult(passed=True)


class CompositeGuardrail:
    """Run multiple guardrails — all must pass."""

    def __init__(self, guards: list[Guardrail]):
        self.guards = guards

    def check(self, output: str, context: dict[str, Any]) -> GuardrailResult:
        failures: list[str] = []
        for g in self.guards:
            result = g.check(output, context)
            if not result.passed and result.feedback:
                failures.append(result.feedback)
        if failures:
            return GuardrailResult(passed=False, feedback=" | ".join(failures))
        return GuardrailResult(passed=True)


@dataclass
class GuardrailRunResult:
    """Result of run_with_guardrails."""

    output: str
    passed: bool
    attempts: int
    failures: list[str] = field(default_factory=list)


def run_with_guardrails(
    fn: Callable[..., str],
    guardrails: list[Guardrail],
    *,
    max_retries: int = 3,
    context: dict[str, Any] | None = None,
    fn_args: tuple = (),
    fn_kwargs: dict[str, Any] | None = None,
) -> GuardrailRunResult:
    """Execute fn and validate output through guardrails, retrying on failure.

    On each retry the accumulated feedback is passed in context["guardrail_feedback"]
    so the caller (typically an LLM wrapper) can self-correct.
    """
    ctx = dict(context or {})
    kwargs = dict(fn_kwargs or {})
    all_failures: list[str] = []

    for attempt in range(1, max_retries + 1):
        output = fn(*fn_args, **kwargs)
        feedbacks: list[str] = []
        all_passed = True

        for guard in guardrails:
            result = guard.check(output, ctx)
            if not result.passed:
                all_passed = False
                if result.feedback:
                    feedbacks.append(result.feedback)

        if all_passed:
            event_bus.emit(
                Event(
                    type=EventType.GUARDRAIL_PASS,
                    payload={"attempt": attempt},
                    source="guardrails",
                )
            )
            return GuardrailRunResult(output=output, passed=True, attempts=attempt)

        combined = " | ".join(feedbacks)
        all_failures.append(f"Attempt {attempt}: {combined}")
        ctx["guardrail_feedback"] = combined

        if attempt < max_retries:
            event_bus.emit(
                Event(
                    type=EventType.GUARDRAIL_RETRY,
                    payload={"attempt": attempt, "feedback": combined},
                    source="guardrails",
                )
            )
        else:
            event_bus.emit(
                Event(
                    type=EventType.GUARDRAIL_FAIL,
                    payload={"attempts": attempt, "failures": all_failures},
                    source="guardrails",
                )
            )

    return GuardrailRunResult(
        output=output,
        passed=False,
        attempts=max_retries,
        failures=all_failures,
    )
