"""Tests for core.guardrails."""

from __future__ import annotations

from unittest.mock import MagicMock

from pydantic import BaseModel

from core.systems.governance.guardrails import (
    CompositeGuardrail,
    GuardrailResult,
    JsonGuardrail,
    LengthGuardrail,
    LLMGuardrail,
    RegexGuardrail,
    run_with_guardrails,
)


class TestLengthGuardrail:
    def test_pass(self):
        g = LengthGuardrail(min_len=5, max_len=100)
        r = g.check("Hello world", {})
        assert r.passed

    def test_too_short(self):
        g = LengthGuardrail(min_len=10)
        r = g.check("Hi", {})
        assert not r.passed
        assert "too short" in r.feedback.lower()

    def test_too_long(self):
        g = LengthGuardrail(max_len=5)
        r = g.check("This is way too long", {})
        assert not r.passed
        assert "too long" in r.feedback.lower()

    def test_exact_boundary(self):
        g = LengthGuardrail(min_len=5, max_len=5)
        assert g.check("12345", {}).passed
        assert not g.check("1234", {}).passed
        assert not g.check("123456", {}).passed


class TestJsonGuardrail:
    def test_valid_json(self):
        g = JsonGuardrail()
        assert g.check('{"key": "value"}', {}).passed

    def test_invalid_json(self):
        g = JsonGuardrail()
        r = g.check("not json", {})
        assert not r.passed
        assert "not valid JSON" in r.feedback

    def test_schema_pass(self):
        class Item(BaseModel):
            name: str
            count: int

        g = JsonGuardrail(schema=Item)
        assert g.check('{"name": "apple", "count": 3}', {}).passed

    def test_schema_fail(self):
        class Item(BaseModel):
            name: str
            count: int

        g = JsonGuardrail(schema=Item)
        r = g.check('{"name": "apple"}', {})
        assert not r.passed
        assert "Item" in r.feedback


class TestRegexGuardrail:
    def test_must_match_pass(self):
        g = RegexGuardrail(r"\d{3}-\d{4}")
        assert g.check("Call 123-4567 now", {}).passed

    def test_must_match_fail(self):
        g = RegexGuardrail(r"\d{3}-\d{4}")
        r = g.check("No phone here", {})
        assert not r.passed

    def test_must_not_match_pass(self):
        g = RegexGuardrail(r"password", must_match=False)
        assert g.check("This is safe content", {}).passed

    def test_must_not_match_fail(self):
        g = RegexGuardrail(r"password", must_match=False)
        r = g.check("Your password is 1234", {})
        assert not r.passed


class TestLLMGuardrail:
    def test_pass(self):
        llm = MagicMock()
        llm.invoke.return_value = MagicMock(content='{"pass": true, "feedback": ""}')
        g = LLMGuardrail("Be polite", llm)
        assert g.check("Thank you!", {}).passed

    def test_fail(self):
        llm = MagicMock()
        llm.invoke.return_value = MagicMock(content='{"pass": false, "feedback": "Not polite enough"}')
        g = LLMGuardrail("Be polite", llm)
        r = g.check("Whatever.", {})
        assert not r.passed
        assert "Not polite" in r.feedback

    def test_llm_error_defaults_to_pass(self):
        llm = MagicMock()
        llm.invoke.side_effect = RuntimeError("LLM down")
        g = LLMGuardrail("Be polite", llm)
        assert g.check("Hello", {}).passed


class TestCompositeGuardrail:
    def test_all_pass(self):
        g = CompositeGuardrail(
            [
                LengthGuardrail(min_len=1),
                RegexGuardrail(r"\w+"),
            ]
        )
        assert g.check("Hello", {}).passed

    def test_one_fails(self):
        g = CompositeGuardrail(
            [
                LengthGuardrail(min_len=100),
                RegexGuardrail(r"\w+"),
            ]
        )
        r = g.check("Hi", {})
        assert not r.passed
        assert "too short" in r.feedback.lower()

    def test_multiple_fail(self):
        g = CompositeGuardrail(
            [
                LengthGuardrail(min_len=100),
                RegexGuardrail(r"\d+"),
            ]
        )
        r = g.check("Hi", {})
        assert not r.passed
        assert "|" in r.feedback  # combined


class TestRunWithGuardrails:
    def test_immediate_pass(self):
        result = run_with_guardrails(
            lambda: "Hello world",
            [LengthGuardrail(min_len=5)],
        )
        assert result.passed
        assert result.attempts == 1
        assert result.output == "Hello world"

    def test_retry_then_pass(self):
        call_count = {"n": 0}

        def flaky():
            call_count["n"] += 1
            if call_count["n"] < 3:
                return "Hi"
            return "Hello world, this is long enough"

        result = run_with_guardrails(
            flaky,
            [LengthGuardrail(min_len=10)],
            max_retries=5,
        )
        assert result.passed
        assert result.attempts == 3

    def test_all_retries_fail(self):
        result = run_with_guardrails(
            lambda: "x",
            [LengthGuardrail(min_len=100)],
            max_retries=2,
        )
        assert not result.passed
        assert result.attempts == 2
        assert len(result.failures) == 2

    def test_context_receives_feedback(self):
        contexts_seen = []

        def fn():
            return "short"

        class SpyGuardrail:
            def check(self, output, context):
                contexts_seen.append(dict(context))
                return GuardrailResult(passed=False, feedback="nope")

        run_with_guardrails(fn, [SpyGuardrail()], max_retries=3)
        assert len(contexts_seen) == 3
        assert "guardrail_feedback" not in contexts_seen[0]
        assert contexts_seen[1]["guardrail_feedback"] == "nope"

    def test_fn_args_and_kwargs(self):
        def fn(a, b, c=10):
            return f"{a}-{b}-{c}"

        result = run_with_guardrails(
            fn,
            [RegexGuardrail(r"1-2-3")],
            fn_args=(1, 2),
            fn_kwargs={"c": 3},
        )
        assert result.passed
        assert result.output == "1-2-3"
