"""Tests for core.context_strategies."""

from __future__ import annotations

from core.systems.context.context_strategies import (
    BufferedChatContext,
    CompositeContextStrategy,
    HeadAndTailChatContext,
    TokenLimitedChatContext,
)


def _msg(role, content):
    return {"role": role, "content": content}


def _conversation(n, *, with_system=True):
    msgs = []
    if with_system:
        msgs.append(_msg("system", "You are a helpful assistant."))
    for i in range(n):
        msgs.append(_msg("user", f"User message {i}"))
        msgs.append(_msg("assistant", f"Assistant response {i}"))
    return msgs


class TestBufferedChatContext:
    def test_under_limit(self):
        msgs = _conversation(3)
        ctx = BufferedChatContext(buffer_size=20)
        result = ctx.apply(msgs)
        assert len(result) == len(msgs)

    def test_trims_old(self):
        msgs = _conversation(10)
        ctx = BufferedChatContext(buffer_size=4)
        result = ctx.apply(msgs)
        non_sys = [m for m in result if m["role"] != "system"]
        assert len(non_sys) == 4
        assert result[0]["role"] == "system"

    def test_preserves_system(self):
        msgs = _conversation(10)
        ctx = BufferedChatContext(buffer_size=2)
        result = ctx.apply(msgs)
        sys_msgs = [m for m in result if m["role"] == "system"]
        assert len(sys_msgs) == 1

    def test_empty(self):
        assert BufferedChatContext().apply([]) == []

    def test_recent_messages_kept(self):
        msgs = _conversation(5)
        ctx = BufferedChatContext(buffer_size=4)
        result = ctx.apply(msgs)
        non_sys = [m for m in result if m["role"] != "system"]
        assert non_sys[-1]["content"] == "Assistant response 4"
        assert non_sys[-2]["content"] == "User message 4"


class TestTokenLimitedChatContext:
    def test_under_limit(self):
        msgs = _conversation(2)
        ctx = TokenLimitedChatContext(max_tokens=100000)
        result = ctx.apply(msgs)
        assert len(result) == len(msgs)

    def test_trims_to_fit(self):
        msgs = _conversation(20)
        ctx = TokenLimitedChatContext(max_tokens=200)
        result = ctx.apply(msgs)
        assert len(result) < len(msgs)
        assert result[0]["role"] == "system"

    def test_preserves_system(self):
        msgs = _conversation(20)
        ctx = TokenLimitedChatContext(max_tokens=100)
        result = ctx.apply(msgs)
        sys_msgs = [m for m in result if m["role"] == "system"]
        assert len(sys_msgs) >= 1

    def test_keeps_recent(self):
        msgs = _conversation(10)
        ctx = TokenLimitedChatContext(max_tokens=300)
        result = ctx.apply(msgs)
        non_sys = [m for m in result if m["role"] != "system"]
        if non_sys:
            assert "9" in non_sys[-1]["content"]

    def test_very_small_budget(self):
        msgs = _conversation(5)
        ctx = TokenLimitedChatContext(max_tokens=10)
        result = ctx.apply(msgs)
        assert all(m["role"] == "system" for m in result)


class TestHeadAndTailChatContext:
    def test_under_limit(self):
        msgs = _conversation(3)
        ctx = HeadAndTailChatContext(head_count=3, tail_count=10)
        result = ctx.apply(msgs)
        assert len(result) == len(msgs)

    def test_keeps_head_and_tail(self):
        msgs = _conversation(10)
        ctx = HeadAndTailChatContext(head_count=2, tail_count=2)
        result = ctx.apply(msgs)
        non_sys = [m for m in result if m["role"] != "system"]
        content_texts = [m["content"] for m in non_sys if "省略" not in m.get("content", "")]
        assert "User message 0" in content_texts
        assert "Assistant response 9" in content_texts

    def test_separator_message(self):
        msgs = _conversation(10)
        ctx = HeadAndTailChatContext(head_count=2, tail_count=2)
        result = ctx.apply(msgs)
        separators = [m for m in result if "省略" in m.get("content", "")]
        assert len(separators) == 1
        assert "16" in separators[0]["content"]  # 20 - 2 - 2 = 16

    def test_preserves_system(self):
        msgs = _conversation(10)
        ctx = HeadAndTailChatContext(head_count=1, tail_count=1)
        result = ctx.apply(msgs)
        assert result[0]["role"] == "system"


class TestCompositeContextStrategy:
    def test_pipeline(self):
        msgs = _conversation(20)
        strategy = CompositeContextStrategy(
            [
                BufferedChatContext(buffer_size=10),
                TokenLimitedChatContext(max_tokens=500),
            ]
        )
        result = strategy.apply(msgs)
        assert len(result) < len(msgs)

    def test_empty_strategies(self):
        msgs = _conversation(3)
        strategy = CompositeContextStrategy([])
        result = strategy.apply(msgs)
        assert result == msgs

    def test_single_strategy(self):
        msgs = _conversation(5)
        strategy = CompositeContextStrategy([BufferedChatContext(buffer_size=4)])
        result = strategy.apply(msgs)
        non_sys = [m for m in result if m["role"] != "system"]
        assert len(non_sys) == 4
