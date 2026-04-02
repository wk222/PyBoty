"""Structured output support for LLM responses.

Enables LLMs to produce responses conforming to Pydantic schemas.
Works with any BaseChatModel that supports .with_structured_output().

Usage:
    from core.structured_output import invoke_structured, AnalysisResult

    class AnalysisResult(BaseModel):
        summary: str
        confidence: float
        tags: list[str]

    result = invoke_structured(llm, messages, AnalysisResult)
"""

from __future__ import annotations

import json
import logging
from typing import TypeVar

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import BaseMessage
from pydantic import BaseModel, Field, ValidationError

logger = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)


class StructuredOutputError(Exception):
    """Raised when structured output extraction fails."""


def invoke_structured(
    llm: BaseChatModel,
    messages: list[BaseMessage],
    schema: type[T],
    *,
    max_retries: int = 2,
    method: str = "auto",
) -> T:
    """Invoke an LLM and parse the response into a Pydantic model.

    Tries the following strategies in order:
    1. .with_structured_output() (native provider support)
    2. JSON mode parsing from raw response
    3. Text extraction with validation

    Args:
        llm: The language model to use.
        messages: Chat messages to send.
        schema: Pydantic model class for the expected response.
        max_retries: Number of retry attempts on parse failure.
        method: Strategy — "auto", "native", "json_mode", or "manual".

    Returns:
        An instance of the schema class.

    Raises:
        StructuredOutputError: If all strategies fail.
    """
    if method == "auto":
        for strategy in [_try_native, _try_json_parse, _try_manual_extract]:
            for attempt in range(max_retries + 1):
                try:
                    return strategy(llm, messages, schema)
                except (StructuredOutputError, ValidationError, Exception) as exc:
                    if attempt == max_retries:
                        logger.debug("Strategy %s failed: %s", strategy.__name__, exc)
                        break
        raise StructuredOutputError(f"All strategies exhausted for schema {schema.__name__}")

    strategy_map = {
        "native": _try_native,
        "json_mode": _try_json_parse,
        "manual": _try_manual_extract,
    }
    fn = strategy_map.get(method)
    if fn is None:
        raise StructuredOutputError(f"Unknown method: {method!r}")

    for attempt in range(max_retries + 1):
        try:
            return fn(llm, messages, schema)
        except (StructuredOutputError, ValidationError, Exception) as exc:
            if attempt == max_retries:
                raise StructuredOutputError(
                    f"Strategy {method!r} failed after {max_retries + 1} attempts: {exc}"
                ) from exc


def _try_native(
    llm: BaseChatModel,
    messages: list[BaseMessage],
    schema: type[T],
) -> T:
    """Use provider's native structured output support."""
    try:
        structured_llm = llm.with_structured_output(schema)
    except (NotImplementedError, AttributeError, TypeError) as exc:
        raise StructuredOutputError(f"Native structured output not supported: {exc}") from exc

    result = structured_llm.invoke(messages)
    if isinstance(result, schema):
        return result
    raise StructuredOutputError(f"Expected {schema.__name__}, got {type(result).__name__}")


def _try_json_parse(
    llm: BaseChatModel,
    messages: list[BaseMessage],
    schema: type[T],
) -> T:
    """Request JSON output and parse into schema."""
    schema_desc = _schema_to_instruction(schema)
    enhanced_messages = list(messages)

    from langchain_core.messages import SystemMessage

    json_instruction = SystemMessage(
        content=(
            f"You MUST respond with a valid JSON object that conforms to this schema:\n"
            f"{schema_desc}\n"
            f"Return ONLY the JSON object, no additional text."
        )
    )
    enhanced_messages.insert(0, json_instruction)

    response = llm.invoke(enhanced_messages)
    text = response.content if hasattr(response, "content") else str(response)

    json_str = _extract_json_from_text(text)
    if json_str is None:
        raise StructuredOutputError("No valid JSON found in response")

    try:
        data = json.loads(json_str)
    except json.JSONDecodeError as exc:
        raise StructuredOutputError(f"Invalid JSON: {exc}") from exc

    return schema.model_validate(data)


def _try_manual_extract(
    llm: BaseChatModel,
    messages: list[BaseMessage],
    schema: type[T],
) -> T:
    """Invoke normally and try to extract structured data from response text."""
    response = llm.invoke(messages)
    text = response.content if hasattr(response, "content") else str(response)

    json_str = _extract_json_from_text(text)
    if json_str:
        try:
            data = json.loads(json_str)
            return schema.model_validate(data)
        except (json.JSONDecodeError, ValidationError):
            pass

    raise StructuredOutputError("Could not extract structured data from response")


def _extract_json_from_text(text: str) -> str | None:
    """Extract a JSON object from text that may contain other content."""
    text = text.strip()

    if text.startswith("{") and text.endswith("}"):
        return text

    fence_start = text.find("```json")
    if fence_start != -1:
        json_start = text.find("\n", fence_start) + 1
        json_end = text.find("```", json_start)
        if json_end != -1:
            return text[json_start:json_end].strip()

    fence_start = text.find("```")
    if fence_start != -1:
        json_start = text.find("\n", fence_start) + 1
        json_end = text.find("```", json_start)
        if json_end != -1:
            candidate = text[json_start:json_end].strip()
            if candidate.startswith("{"):
                return candidate

    brace_start = text.find("{")
    brace_end = text.rfind("}")
    if brace_start != -1 and brace_end > brace_start:
        return text[brace_start : brace_end + 1]

    return None


def _schema_to_instruction(schema: type[BaseModel]) -> str:
    """Convert a Pydantic model to a human-readable schema description."""
    try:
        json_schema = schema.model_json_schema()
        return json.dumps(json_schema, indent=2, ensure_ascii=False)
    except Exception:
        fields_desc = []
        for name, field_info in schema.model_fields.items():
            annotation = field_info.annotation
            desc = field_info.description or ""
            fields_desc.append(f"  {name}: {annotation} — {desc}")
        return "{\n" + "\n".join(fields_desc) + "\n}"


class TaskAnalysis(BaseModel):
    """Built-in schema: structured task analysis."""

    summary: str = Field(description="任务摘要")
    steps: list[str] = Field(description="执行步骤列表")
    complexity: str = Field(description="复杂度: low/medium/high")
    estimated_minutes: int = Field(description="预估耗时(分钟)")


class CodeReview(BaseModel):
    """Built-in schema: structured code review."""

    issues: list[str] = Field(default_factory=list, description="发现的问题列表")
    suggestions: list[str] = Field(default_factory=list, description="改进建议列表")
    quality_score: int = Field(ge=1, le=10, description="代码质量评分 1-10")
    summary: str = Field(description="总体评价")
