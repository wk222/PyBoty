"""Tests for core.termination."""

from __future__ import annotations

import threading

from core.systems.runtime.termination import (
    AllConditions,
    AnyCondition,
    ExternalSignal,
    FunctionalCondition,
    MaxMessages,
    MaxTokens,
    ScoreThreshold,
    TerminationContext,
    TextMatch,
    Timeout,
)


def _ctx(**kwargs) -> TerminationContext:
    return TerminationContext(**kwargs)


class TestMaxMessages:
    def test_under_limit(self):
        c = MaxMessages(10)
        assert not c.check(_ctx(messages_count=5))

    def test_at_limit(self):
        c = MaxMessages(10)
        assert c.check(_ctx(messages_count=10))

    def test_over_limit(self):
        c = MaxMessages(5)
        assert c.check(_ctx(messages_count=100))

    def test_reason(self):
        assert "10" in MaxMessages(10).reason


class TestMaxTokens:
    def test_under(self):
        assert not MaxTokens(1000).check(_ctx(token_usage=500))

    def test_at(self):
        assert MaxTokens(1000).check(_ctx(token_usage=1000))


class TestTimeout:
    def test_under(self):
        assert not Timeout(60).check(_ctx(elapsed_seconds=30))

    def test_over(self):
        assert Timeout(60).check(_ctx(elapsed_seconds=120))


class TestTextMatch:
    def test_match(self):
        c = TextMatch(r"TERMINATE")
        assert c.check(_ctx(last_output="Please TERMINATE now"))

    def test_no_match(self):
        c = TextMatch(r"TERMINATE")
        assert not c.check(_ctx(last_output="keep going"))

    def test_regex(self):
        c = TextMatch(r"\d{3}-\d{4}")
        assert c.check(_ctx(last_output="Call 555-1234"))
        assert not c.check(_ctx(last_output="no number"))


class TestScoreThreshold:
    def test_meets(self):
        c = ScoreThreshold("accuracy", 0.9)
        assert c.check(_ctx(custom_data={"accuracy": 0.95}))

    def test_below(self):
        c = ScoreThreshold("accuracy", 0.9)
        assert not c.check(_ctx(custom_data={"accuracy": 0.5}))

    def test_missing_key(self):
        c = ScoreThreshold("accuracy", 0.9)
        assert not c.check(_ctx())

    def test_non_numeric(self):
        c = ScoreThreshold("val", 1.0)
        assert not c.check(_ctx(custom_data={"val": "not_a_number"}))


class TestExternalSignal:
    def test_not_set(self):
        c = ExternalSignal()
        assert not c.check(_ctx())

    def test_signal(self):
        c = ExternalSignal()
        c.signal()
        assert c.check(_ctx())

    def test_reset(self):
        c = ExternalSignal()
        c.signal()
        c.reset()
        assert not c.check(_ctx())

    def test_with_existing_event(self):
        ev = threading.Event()
        c = ExternalSignal(ev)
        assert not c.check(_ctx())
        ev.set()
        assert c.check(_ctx())


class TestFunctionalCondition:
    def test_true(self):
        c = FunctionalCondition(lambda ctx: ctx.messages_count > 3, "msg > 3")
        assert c.check(_ctx(messages_count=5))

    def test_false(self):
        c = FunctionalCondition(lambda ctx: ctx.messages_count > 3, "msg > 3")
        assert not c.check(_ctx(messages_count=1))

    def test_reason(self):
        c = FunctionalCondition(lambda ctx: True, "always")
        assert "always" in c.reason


class TestAnyCondition:
    def test_one_met(self):
        c = AnyCondition(MaxMessages(10), Timeout(60))
        assert c.check(_ctx(messages_count=20, elapsed_seconds=30))

    def test_none_met(self):
        c = AnyCondition(MaxMessages(10), Timeout(60))
        assert not c.check(_ctx(messages_count=5, elapsed_seconds=30))

    def test_reason_shows_triggered(self):
        c = AnyCondition(MaxMessages(10), Timeout(60))
        c.check(_ctx(messages_count=20, elapsed_seconds=30))
        assert "Max messages" in c.reason


class TestAllConditions:
    def test_all_met(self):
        c = AllConditions(MaxMessages(10), Timeout(60))
        assert c.check(_ctx(messages_count=20, elapsed_seconds=120))

    def test_partial(self):
        c = AllConditions(MaxMessages(10), Timeout(60))
        assert not c.check(_ctx(messages_count=20, elapsed_seconds=30))

    def test_reason(self):
        c = AllConditions(MaxMessages(5), Timeout(30))
        assert "All:" in c.reason


class TestComposition:
    def test_nested(self):
        inner = AllConditions(MaxMessages(10), MaxTokens(5000))
        outer = AnyCondition(inner, Timeout(3600))
        assert not outer.check(_ctx(messages_count=20, token_usage=100, elapsed_seconds=10))
        assert outer.check(_ctx(messages_count=20, token_usage=6000, elapsed_seconds=10))
        assert outer.check(_ctx(messages_count=1, token_usage=0, elapsed_seconds=7200))
