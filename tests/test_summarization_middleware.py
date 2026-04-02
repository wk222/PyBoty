"""Tests for SummarizationMiddleware."""

from __future__ import annotations

import pytest

try:
    from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

    _HAS_LC = True
except ImportError:
    _HAS_LC = False

from core.systems.middleware.summarization_middleware import (
    SummarizationConfig,
    SummarizationMiddleware,
    _count_message_tokens,
)

pytestmark = pytest.mark.skipif(not _HAS_LC, reason="langchain not installed")


def _human(text: str) -> HumanMessage:
    return HumanMessage(content=text)


def _ai(text: str, tool_calls: list | None = None) -> AIMessage:
    return AIMessage(content=text, tool_calls=tool_calls or [])


class TestTokenCounting:
    def test_empty_messages(self):
        assert _count_message_tokens([]) == 0

    def test_string_content(self):
        tokens = _count_message_tokens([_human("hello world")])
        assert tokens > 0

    def test_tool_call_args_counted(self):
        ai = _ai("", tool_calls=[{"id": "tc1", "name": "foo", "args": {"x": "a" * 100}}])
        tokens = _count_message_tokens([ai])
        assert tokens > 20


class TestToolArgTruncation:
    def test_truncates_large_write_file_args(self):
        config = SummarizationConfig(
            tool_arg_trigger_messages=2,
            keep_recent_messages=1,
            max_tool_arg_chars=50,
        )
        mw = SummarizationMiddleware(config=config)
        big_content = "x" * 200
        ai = _ai(
            "",
            tool_calls=[
                {"id": "tc1", "name": "write_file", "args": {"content": big_content}},
            ],
        )
        tool_resp = ToolMessage(content="ok", tool_call_id="tc1")
        human = _human("done")
        messages = [ai, tool_resp, human]
        result = mw._truncate_tool_args(messages)
        first_ai = result[0]
        assert len(first_ai.tool_calls[0]["args"]["content"]) < 100

    def test_no_truncation_below_trigger(self):
        config = SummarizationConfig(
            tool_arg_trigger_messages=100,
            keep_recent_messages=5,
        )
        mw = SummarizationMiddleware(config=config)
        ai = _ai(
            "",
            tool_calls=[
                {"id": "tc1", "name": "write_file", "args": {"content": "x" * 5000}},
            ],
        )
        result = mw._truncate_tool_args([ai])
        assert result[0].tool_calls[0]["args"]["content"] == "x" * 5000

    def test_preserves_recent_messages(self):
        config = SummarizationConfig(
            tool_arg_trigger_messages=2,
            keep_recent_messages=2,
            max_tool_arg_chars=50,
        )
        mw = SummarizationMiddleware(config=config)
        old_ai = _ai(
            "",
            tool_calls=[
                {"id": "tc1", "name": "write_file", "args": {"content": "x" * 200}},
            ],
        )
        new_ai = _ai(
            "",
            tool_calls=[
                {"id": "tc2", "name": "write_file", "args": {"content": "y" * 200}},
            ],
        )
        human = _human("end")
        result = mw._truncate_tool_args([old_ai, new_ai, human])
        assert len(result[0].tool_calls[0]["args"]["content"]) < 100
        assert result[1].tool_calls[0]["args"]["content"] == "y" * 200

    def test_non_truncatable_tools_ignored(self):
        config = SummarizationConfig(
            tool_arg_trigger_messages=2,
            keep_recent_messages=1,
            max_tool_arg_chars=50,
        )
        mw = SummarizationMiddleware(config=config)
        ai = _ai(
            "",
            tool_calls=[
                {"id": "tc1", "name": "execute", "args": {"cmd": "x" * 200}},
            ],
        )
        result = mw._truncate_tool_args([ai, _human("end")])
        assert result[0].tool_calls[0]["args"]["cmd"] == "x" * 200


class TestSummarization:
    def test_llm_summary_used_when_available(self):
        config = SummarizationConfig(keep_recent_messages=2)
        mw = SummarizationMiddleware(
            summarize_fn=lambda prompt: "LLM summary result",
            config=config,
        )
        messages = [_human(f"msg {i}") for i in range(10)]
        count = mw._do_summarize(messages)
        assert count == 8
        assert mw._summary_message is not None
        assert "LLM summary result" in mw._summary_message.content

    def test_fallback_summary_when_no_llm(self):
        config = SummarizationConfig(keep_recent_messages=2)
        mw = SummarizationMiddleware(config=config)
        messages = [_human(f"topic {i}") for i in range(5)]
        count = mw._do_summarize(messages)
        assert count == 3
        assert mw._summary_message is not None
        assert "Topics discussed" in mw._summary_message.content

    def test_no_summarize_when_within_budget(self):
        config = SummarizationConfig(keep_recent_messages=20)
        mw = SummarizationMiddleware(config=config)
        messages = [_human("short")]
        count = mw._do_summarize(messages)
        assert count is None

    def test_llm_failure_falls_back(self):
        def failing_fn(prompt: str) -> str:
            raise RuntimeError("API down")

        config = SummarizationConfig(keep_recent_messages=2)
        mw = SummarizationMiddleware(summarize_fn=failing_fn, config=config)
        messages = [_human(f"topic {i}") for i in range(5)]
        count = mw._do_summarize(messages)
        assert count == 3
        assert "Topics discussed" in mw._summary_message.content

    def test_compaction_callback_receives_summary_payload(self):
        payloads = []
        config = SummarizationConfig(keep_recent_messages=2, thread_id="thread-callback")
        mw = SummarizationMiddleware(
            summarize_fn=lambda prompt: "callback summary result",
            config=config,
            compaction_callback=payloads.append,
        )
        messages = [_human(f"topic {i}") for i in range(6)]
        count = mw._do_summarize(messages)
        assert count == 4
        assert payloads
        assert payloads[-1]["thread_id"] == "thread-callback"
        assert payloads[-1]["summary"] == "callback summary result"


class TestOffload:
    def test_offload_creates_file(self, tmp_path):
        config = SummarizationConfig(
            offload_dir=str(tmp_path),
            thread_id="test_thread",
        )
        mw = SummarizationMiddleware(config=config)
        mw._offload([_human("hello"), _ai("world")])
        offload_file = tmp_path / "test_thread.md"
        assert offload_file.exists()
        content = offload_file.read_text(encoding="utf-8")
        assert "Summarized at" in content

    def test_offload_appends(self, tmp_path):
        config = SummarizationConfig(
            offload_dir=str(tmp_path),
            thread_id="append_test",
        )
        mw = SummarizationMiddleware(config=config)
        mw._offload([_human("first")])
        mw._offload([_human("second")])
        content = (tmp_path / "append_test.md").read_text(encoding="utf-8")
        assert content.count("Summarized at") == 2

    def test_offload_filters_summary_messages(self, tmp_path):
        config = SummarizationConfig(
            offload_dir=str(tmp_path),
            thread_id="filter_test",
        )
        mw = SummarizationMiddleware(config=config)
        summary_msg = HumanMessage(
            content="old summary",
            additional_kwargs={"lc_source": "summarization"},
        )
        mw._offload([summary_msg, _human("real content")])
        content = (tmp_path / "filter_test.md").read_text(encoding="utf-8")
        assert "old summary" not in content
        assert "real content" in content


class TestEffectiveMessages:
    def test_no_summary_returns_all(self):
        mw = SummarizationMiddleware()
        msgs = [_human("a"), _human("b")]
        assert mw._get_effective_messages(msgs) == msgs

    def test_with_summary_slices_from_cutoff(self):
        mw = SummarizationMiddleware()
        mw._summary_message = _human("summary")
        mw._cutoff_index = 3
        msgs = [_human(f"m{i}") for i in range(5)]
        effective = mw._get_effective_messages(msgs)
        assert len(effective) == 3
        assert effective[0].content == "summary"


class TestCompactTool:
    def test_compact_tool_exists(self):
        mw = SummarizationMiddleware()
        assert len(mw.tools) == 1
        assert mw.tools[0].name == "compact_conversation"

    def test_compact_when_short_returns_nothing(self):
        mw = SummarizationMiddleware()
        mw._last_messages = [_human("hi")]
        result = mw._run_compact()
        assert "Nothing" in result or "short" in result
