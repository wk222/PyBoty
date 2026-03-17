"""Tests for core.structured_output — Pydantic schema response parsing."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, HumanMessage
from pydantic import BaseModel, Field

from core.structured_output import (
    CodeReview,
    StructuredOutputError,
    TaskAnalysis,
    _extract_json_from_text,
    _schema_to_instruction,
    invoke_structured,
)


class SimpleSchema(BaseModel):
    name: str
    value: int


class DetailedSchema(BaseModel):
    title: str = Field(description="标题")
    items: list[str] = Field(default_factory=list, description="项目列表")
    score: float = Field(default=0.0)


class TestExtractJson:
    def test_pure_json(self):
        result = _extract_json_from_text('{"name": "test", "value": 42}')
        assert result == '{"name": "test", "value": 42}'

    def test_json_in_code_fence(self):
        text = 'Here is the result:\n```json\n{"name": "test", "value": 42}\n```'
        result = _extract_json_from_text(text)
        assert '"test"' in result

    def test_json_in_plain_fence(self):
        text = 'Result:\n```\n{"name": "x", "value": 1}\n```'
        result = _extract_json_from_text(text)
        assert '"x"' in result

    def test_json_embedded_in_text(self):
        text = 'The answer is {"name": "embedded", "value": 99} and that is it.'
        result = _extract_json_from_text(text)
        assert result is not None
        assert "embedded" in result

    def test_no_json(self):
        result = _extract_json_from_text("No JSON here at all")
        assert result is None

    def test_empty_string(self):
        result = _extract_json_from_text("")
        assert result is None


class TestSchemaToInstruction:
    def test_produces_json_schema(self):
        result = _schema_to_instruction(SimpleSchema)
        assert "name" in result
        assert "value" in result

    def test_detailed_schema(self):
        result = _schema_to_instruction(DetailedSchema)
        assert "title" in result


class TestInvokeStructuredNative:
    def test_native_success(self):
        mock_structured = MagicMock()
        mock_structured.invoke.return_value = SimpleSchema(name="test", value=42)

        mock_llm = MagicMock(spec=BaseChatModel)
        mock_llm.with_structured_output.return_value = mock_structured

        messages = [HumanMessage(content="hi")]
        result = invoke_structured(mock_llm, messages, SimpleSchema, method="native")
        assert result.name == "test"
        assert result.value == 42

    def test_native_not_supported(self):
        mock_llm = MagicMock(spec=BaseChatModel)
        mock_llm.with_structured_output.side_effect = NotImplementedError("nope")

        messages = [HumanMessage(content="hi")]
        with pytest.raises(StructuredOutputError):
            invoke_structured(mock_llm, messages, SimpleSchema, method="native", max_retries=0)


class TestInvokeStructuredJsonMode:
    def test_json_mode_success(self):
        mock_llm = MagicMock(spec=BaseChatModel)
        mock_llm.invoke.return_value = AIMessage(content='{"name": "parsed", "value": 99}')

        messages = [HumanMessage(content="test")]
        result = invoke_structured(mock_llm, messages, SimpleSchema, method="json_mode")
        assert result.name == "parsed"
        assert result.value == 99

    def test_json_mode_with_code_fence(self):
        mock_llm = MagicMock(spec=BaseChatModel)
        mock_llm.invoke.return_value = AIMessage(content='Sure:\n```json\n{"name": "fenced", "value": 7}\n```')

        messages = [HumanMessage(content="test")]
        result = invoke_structured(mock_llm, messages, SimpleSchema, method="json_mode")
        assert result.name == "fenced"

    def test_json_mode_invalid_json(self):
        mock_llm = MagicMock(spec=BaseChatModel)
        mock_llm.invoke.return_value = AIMessage(content="not json at all")

        with pytest.raises(StructuredOutputError):
            invoke_structured(
                mock_llm,
                [HumanMessage(content="test")],
                SimpleSchema,
                method="json_mode",
                max_retries=0,
            )


class TestInvokeStructuredManual:
    def test_manual_extracts_json(self):
        mock_llm = MagicMock(spec=BaseChatModel)
        mock_llm.invoke.return_value = AIMessage(content='Here is your data: {"name": "manual", "value": 5}')

        result = invoke_structured(
            mock_llm,
            [HumanMessage(content="test")],
            SimpleSchema,
            method="manual",
        )
        assert result.name == "manual"

    def test_manual_fails_no_json(self):
        mock_llm = MagicMock(spec=BaseChatModel)
        mock_llm.invoke.return_value = AIMessage(content="Just text, no JSON.")

        with pytest.raises(StructuredOutputError):
            invoke_structured(
                mock_llm,
                [HumanMessage(content="test")],
                SimpleSchema,
                method="manual",
                max_retries=0,
            )


class TestInvokeStructuredAuto:
    def test_auto_tries_strategies(self):
        mock_llm = MagicMock(spec=BaseChatModel)
        mock_llm.with_structured_output.side_effect = NotImplementedError("nope")
        mock_llm.invoke.return_value = AIMessage(content='{"name": "auto", "value": 1}')

        result = invoke_structured(mock_llm, [HumanMessage(content="test")], SimpleSchema)
        assert result.name == "auto"

    def test_auto_all_fail(self):
        mock_llm = MagicMock(spec=BaseChatModel)
        mock_llm.with_structured_output.side_effect = NotImplementedError("nope")
        mock_llm.invoke.return_value = AIMessage(content="no json here")

        with pytest.raises(StructuredOutputError, match="All strategies exhausted"):
            invoke_structured(
                mock_llm,
                [HumanMessage(content="test")],
                SimpleSchema,
                max_retries=0,
            )


class TestInvokeUnknownMethod:
    def test_unknown_method(self):
        mock_llm = MagicMock(spec=BaseChatModel)
        with pytest.raises(StructuredOutputError, match="Unknown method"):
            invoke_structured(
                mock_llm,
                [HumanMessage(content="test")],
                SimpleSchema,
                method="nonexistent",
            )


class TestBuiltinSchemas:
    def test_task_analysis_schema(self):
        ta = TaskAnalysis(
            summary="Build a feature",
            steps=["Step 1", "Step 2"],
            complexity="medium",
            estimated_minutes=30,
        )
        assert ta.complexity == "medium"

    def test_code_review_schema(self):
        cr = CodeReview(
            issues=["Bug in line 10"],
            suggestions=["Add tests"],
            quality_score=7,
            summary="Good overall",
        )
        assert cr.quality_score == 7
