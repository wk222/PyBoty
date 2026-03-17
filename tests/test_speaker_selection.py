"""Tests for core.speaker_selection."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from core.speaker_selection import (
    ChatMessage,
    LLMSelector,
    Participant,
    RandomSelector,
    RoundRobinSelector,
    RuleBasedSelector,
)


def _participants():
    return [
        Participant("Alice", role="researcher", description="Expert in data analysis"),
        Participant("Bob", role="developer", description="Backend specialist"),
        Participant("Carol", role="designer", description="UI/UX expert"),
    ]


def _history():
    return [
        ChatMessage("Alice", "I found some interesting patterns in the data."),
        ChatMessage("Bob", "Let me write an API for that."),
    ]


class TestRoundRobinSelector:
    def test_cycles_through(self):
        s = RoundRobinSelector()
        p = _participants()
        names = [s.select(p, []) for _ in range(6)]
        assert names == ["Alice", "Bob", "Carol", "Alice", "Bob", "Carol"]

    def test_empty_raises(self):
        s = RoundRobinSelector()
        with pytest.raises(ValueError):
            s.select([], [])


class TestRandomSelector:
    def test_selects_from_participants(self):
        s = RandomSelector()
        p = _participants()
        for _ in range(20):
            name = s.select(p, [])
            assert name in {"Alice", "Bob", "Carol"}

    def test_no_repeat(self):
        s = RandomSelector(allow_repeat=False)
        p = _participants()
        hist = [ChatMessage("Bob", "just said something")]
        for _ in range(20):
            name = s.select(p, hist)
            assert name != "Bob"

    def test_empty_raises(self):
        with pytest.raises(ValueError):
            RandomSelector().select([], [])


class TestRuleBasedSelector:
    def test_keyword_match(self):
        s = RuleBasedSelector({"data": "Alice", "api": "Bob", "design": "Carol"})
        p = _participants()
        hist = [ChatMessage("x", "We need to update the API endpoint")]
        assert s.select(p, hist) == "Bob"

    def test_no_match_returns_default(self):
        s = RuleBasedSelector({"data": "Alice"}, default="Carol")
        p = _participants()
        hist = [ChatMessage("x", "nothing relevant")]
        assert s.select(p, hist) == "Carol"

    def test_no_match_no_default(self):
        s = RuleBasedSelector({"data": "Alice"})
        p = _participants()
        hist = [ChatMessage("x", "nothing")]
        assert s.select(p, hist) == "Alice"  # falls back to first

    def test_empty_history(self):
        s = RuleBasedSelector({"data": "Alice"}, default="Bob")
        p = _participants()
        assert s.select(p, []) == "Bob"

    def test_case_insensitive(self):
        s = RuleBasedSelector({"DATA": "Alice"})
        p = _participants()
        hist = [ChatMessage("x", "need data analysis")]
        assert s.select(p, hist) == "Alice"


class TestLLMSelector:
    def _mock_llm(self, response: str):
        llm = MagicMock()
        result = MagicMock()
        result.content = response
        llm.invoke.return_value = result
        return llm

    def test_valid_selection(self):
        llm = self._mock_llm("Alice")
        s = LLMSelector(llm)
        p = _participants()
        assert s.select(p, _history()) == "Alice"

    def test_case_insensitive_parse(self):
        llm = self._mock_llm("bob")
        s = LLMSelector(llm, allow_repeat=True)
        p = _participants()
        assert s.select(p, _history()) == "Bob"

    def test_name_in_sentence(self):
        llm = self._mock_llm("I think Carol should speak next")
        s = LLMSelector(llm, allow_repeat=True)
        p = _participants()
        assert s.select(p, _history()) == "Carol"

    def test_fallback_on_invalid(self):
        llm = self._mock_llm("UnknownAgent")
        s = LLMSelector(llm, max_attempts=1)
        p = _participants()
        name = s.select(p, _history())
        assert name in {"Alice", "Bob", "Carol"}

    def test_fallback_on_exception(self):
        llm = MagicMock()
        llm.invoke.side_effect = Exception("LLM down")
        s = LLMSelector(llm, max_attempts=1)
        p = _participants()
        name = s.select(p, _history())
        assert name in {"Alice", "Bob", "Carol"}

    def test_single_participant(self):
        llm = self._mock_llm("whatever")
        s = LLMSelector(llm)
        p = [Participant("Solo")]
        assert s.select(p, []) == "Solo"

    def test_no_repeat(self):
        llm = self._mock_llm("Bob")
        s = LLMSelector(llm, allow_repeat=False, max_attempts=1)
        p = _participants()
        hist = [ChatMessage("Bob", "I just spoke")]
        name = s.select(p, hist)
        assert name != "Bob"  # should fall back

    def test_callable_llm(self):
        s = LLMSelector(lambda prompt: "Carol", allow_repeat=True)
        p = _participants()
        assert s.select(p, _history()) == "Carol"

    def test_empty_raises(self):
        llm = self._mock_llm("x")
        with pytest.raises(ValueError):
            LLMSelector(llm).select([], [])

    def test_prompt_building(self):
        llm = self._mock_llm("Alice")
        s = LLMSelector(llm, history_window=2)
        p = _participants()
        s.select(p, _history())
        call_args = llm.invoke.call_args[0][0]
        assert "Alice" in call_args
        assert "Bob" in call_args
        assert "researcher" in call_args
