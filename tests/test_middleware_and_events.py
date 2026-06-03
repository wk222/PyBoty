"""Tests for PyBot Middlewares and Event System.

Consolidates:
1. test_lc_memory_middleware.py
2. test_summarization_middleware.py
3. test_todo_middleware.py
4. test_bus_middleware_legacy.py
5. test_event_bus.py
6. test_hooks_runtime.py
7. test_patch_tool_calls.py
"""

from __future__ import annotations

import asyncio
import threading
import time
from unittest.mock import MagicMock

import pytest

# Check Langchain availability
try:
    from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
    _HAS_LC = True
except ImportError:
    _HAS_LC = False


# Helper factories for LangChain messages
def _human(text: str) -> HumanMessage:
    if not _HAS_LC:
        raise RuntimeError("LangChain not installed")
    return HumanMessage(content=text)


def _ai(text: str, tool_calls: list | None = None) -> AIMessage:
    if not _HAS_LC:
        raise RuntimeError("LangChain not installed")
    return AIMessage(content=text, tool_calls=tool_calls or [])


# Imports from core
from core.systems.middleware.lc_memory_middleware import LCMemoryMiddleware
from core.systems.middleware.summarization_middleware import (
    SummarizationConfig,
    SummarizationMiddleware,
    _count_message_tokens,
)
from core.systems.middleware.todo_middleware import TodoItem, TodoListMiddleware, TodoState
from core.systems.runtime.task_runtime import TaskRuntimeService
from core.systems.middleware.middleware_stack import BusMiddleware
from core.systems.runtime.event_bus import Event, EventBus, EventType, event_bus
from core.systems.runtime.hooks_runtime import HookPhase, create_default_hooks_runtime
from core.systems.runtime.patch_tool_calls import PatchToolCallsMiddleware


# ── Section 1: LC Memory Middleware ────────────────────────────────────

@pytest.mark.skipif(not _HAS_LC, reason="langchain not installed")
class TestMemoryExtraction:
    def test_extracts_via_auto_capture(self):
        memory = MagicMock()
        memory.auto_capture.return_value = ["fact1"]
        mw = LCMemoryMiddleware(memory, auto_capture=True)
        messages = [
            HumanMessage(content="Remember X"),
            AIMessage(content="Got it"),
        ]
        mw._extract_memories(messages)
        memory.auto_capture.assert_called_once()

    def test_skips_short_conversation(self):
        memory = MagicMock()
        mw = LCMemoryMiddleware(memory)
        mw._extract_memories([HumanMessage(content="hi")])
        memory.auto_capture.assert_not_called()

    def test_skips_empty_messages(self):
        memory = MagicMock()
        mw = LCMemoryMiddleware(memory)
        mw._extract_memories([])
        memory.auto_capture.assert_not_called()

    def test_handles_capture_failure_silently(self):
        memory = MagicMock()
        memory.auto_capture.side_effect = RuntimeError("boom")
        mw = LCMemoryMiddleware(memory)
        messages = [
            HumanMessage(content="test"),
            AIMessage(content="response"),
        ]
        # Should not raise — middleware swallows extraction errors.
        mw._extract_memories(messages)

    def test_no_capture_when_disabled(self):
        memory = MagicMock()
        mw = LCMemoryMiddleware(memory, auto_capture=False)
        messages = [
            HumanMessage(content="Remember X"),
            AIMessage(content="Got it"),
        ]
        mw._extract_memories(messages)
        memory.auto_capture.assert_not_called()

    def test_skips_when_no_auto_capture_method(self):
        memory = MagicMock(spec=["load"])  # no auto_capture
        mw = LCMemoryMiddleware(memory, auto_capture=True)
        messages = [
            HumanMessage(content="x"),
            AIMessage(content="y"),
        ]
        # Should silently no-op.
        mw._extract_memories(messages)

    def test_name_property(self):
        mw = LCMemoryMiddleware(MagicMock())
        assert mw.name == "LCMemoryMiddleware"

    def test_extracts_via_memory_engine_auto_capture(self, tmp_path):
        from core.systems.memory import MemoryEngine

        eng = MemoryEngine(tmp_path / "memory")
        try:
            mw = LCMemoryMiddleware(eng, auto_capture=True)
            messages = [
                HumanMessage(content="Remember the architecture."),
                AIMessage(content="PyBot consolidates memory into a single SQLite engine."),
            ]
            mw._extract_memories(messages)
            stats = eng.get_memory_stats()
            assert stats.get("total", 0) >= 1
        finally:
            eng.close()


# ── Section 2: Summarization Middleware ────────────────────────────────

@pytest.mark.skipif(not _HAS_LC, reason="langchain not installed")
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


@pytest.mark.skipif(not _HAS_LC, reason="langchain not installed")
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


@pytest.mark.skipif(not _HAS_LC, reason="langchain not installed")
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

    def test_summary_prefers_resume_bundle_over_session_notes(self):
        config = SummarizationConfig(keep_recent_messages=2)

        class DummySessionExtractor:
            def get_notes(self):
                return "legacy session notes"

        mw = SummarizationMiddleware(
            summarize_fn=lambda prompt: "LLM summary result",
            config=config,
            session_memory_extractor=DummySessionExtractor(),
            runtime_view_provider=lambda: {
                "system_context": {"thread_id": "thread-1", "primary_mode": "assistant"},
                "session": {},
                "workspace": {},
                "tasks": {
                    "summary": "1 active task, 0 completed, 1 recent activities",
                    "tasks": [{"id": "t1", "content": "stabilize trunk", "status": "in_progress"}],
                    "activities": [
                        {"activity_id": "run-1", "kind": "tool_run", "title": "read_file", "status": "completed"}
                    ],
                },
                "permission": {},
                "settings": {},
                "capability": {},
                "context_hygiene": {},
                "hooks": {},
                "route": {},
                "isolation": {},
            },
        )
        messages = [_human(f"msg {i}") for i in range(6)]

        count = mw._do_summarize(messages)

        assert count == 4
        assert mw._summary_message is not None
        content = mw._summary_message.content
        assert "Resume bundle" in content
        assert "## Task Runtime" in content
        assert "## Recent Activity" in content
        assert "legacy session notes" not in content

    def test_cutoff_index_advances_without_duplicating_boundary_message(self):
        config = SummarizationConfig(keep_recent_messages=20, mid_tier_messages=14)
        mw = SummarizationMiddleware(
            summarize_fn=lambda prompt: "LLM summary result",
            config=config,
        )
        messages = [_human(f"msg {i}") for i in range(30)]

        count = mw._do_summarize(messages)
        effective = mw._get_effective_messages(messages)

        assert count == 10
        assert mw._cutoff_index == 10
        assert len(effective) == 21
        assert effective[0].content.startswith("[Previous conversation summarized]")
        assert effective[1].content == "msg 10"

        more_messages = messages + [_human(f"msg {i}") for i in range(30, 40)]
        next_count = mw._do_summarize(mw._get_effective_messages(more_messages))
        next_effective = mw._get_effective_messages(more_messages)

        assert next_count == 11
        assert mw._cutoff_index == 20
        assert len(next_effective) == 21
        assert next_effective[1].content == "msg 20"


@pytest.mark.skipif(not _HAS_LC, reason="langchain not installed")
class TestSessionExtractorTicks:
    class DummySessionExtractor:
        def __init__(self) -> None:
            self.deltas: list[int] = []

        def tick(self, messages, tool_call_delta: int = 0, current_token_count: int = 0) -> None:
            self.deltas.append(tool_call_delta)

        def get_notes(self):
            return None

    def test_tool_call_delta_survives_compacted_effective_view(self):
        extractor = self.DummySessionExtractor()
        mw = SummarizationMiddleware(session_memory_extractor=extractor)

        raw_messages = [
            _ai("", tool_calls=[{"id": "tc1", "name": "read_file", "args": {"path": "a.py"}}]),
            _human("first"),
            _ai("", tool_calls=[{"id": "tc2", "name": "grep_files", "args": {"pattern": "TODO"}}]),
        ]
        effective_messages = [
            _human("[Previous conversation summarized]"),
            raw_messages[-1],
        ]

        mw._tick_session_extractor(raw_messages, effective_messages, total_tokens=100)

        raw_messages_2 = raw_messages + [
            _ai("", tool_calls=[{"id": "tc3", "name": "write_file", "args": {"path": "b.py"}}]),
        ]
        effective_messages_2 = [
            _human("[Previous conversation summarized]"),
            raw_messages_2[-1],
        ]

        mw._tick_session_extractor(raw_messages_2, effective_messages_2, total_tokens=140)

        assert extractor.deltas == [2, 1]


@pytest.mark.skipif(not _HAS_LC, reason="langchain not installed")
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


@pytest.mark.skipif(not _HAS_LC, reason="langchain not installed")
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


@pytest.mark.skipif(not _HAS_LC, reason="langchain not installed")
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

    def test_microcompact_stub_preserves_tool_invocation_preview(self):
        config = SummarizationConfig(keep_recent_messages=2, microcompact_age=2)
        mw = SummarizationMiddleware(config=config)
        messages = [
            _ai("", tool_calls=[{"id": "tc-read", "name": "read_file", "args": {"path": "src/app.py"}}]),
            ToolMessage(content=("line\n" * 300), tool_call_id="tc-read", name="read_file"),
            _human("step 1"),
            _human("step 2"),
            _human("step 3"),
        ]

        compacted = mw._microcompact(messages)

        assert compacted[1].content.startswith("[microcompact]")
        assert "read_file(path=src/app.py)" in compacted[1].content

    def test_compact_callback_receives_microcompact_count(self):
        payloads: list[dict] = []
        config = SummarizationConfig(keep_recent_messages=2, microcompact_age=2)
        mw = SummarizationMiddleware(
            summarize_fn=lambda prompt: "compacted summary",
            config=config,
            compaction_callback=payloads.append,
        )
        mw._last_messages = [
            _ai("", tool_calls=[{"id": "tc-read", "name": "read_file", "args": {"path": "src/app.py"}}]),
            ToolMessage(content=("line\n" * 300), tool_call_id="tc-read", name="read_file"),
            _human("step 1"),
            _human("step 2"),
            _human("step 3"),
            _human("step 4"),
        ]

        result = mw._run_compact()

        assert "Compacted 4 messages into a summary." == result
        assert payloads
        assert payloads[-1]["microcompact_count"] == 1


# ── Section 3: Todo Middleware ─────────────────────────────────────────

class TestTodoState:
    def test_upsert_creates_items(self):
        state = TodoState()
        result = state.upsert(
            [
                {"id": "1", "content": "task A", "status": "pending"},
                {"id": "2", "content": "task B", "status": "in_progress"},
            ]
        )
        assert len(state.items) == 2
        assert "task A" in result
        assert "task B" in result

    def test_upsert_updates_existing(self):
        state = TodoState()
        state.upsert([{"id": "1", "content": "original", "status": "pending"}])
        state.upsert([{"id": "1", "status": "completed"}])
        assert state.items[0].status == "completed"
        assert state.items[0].content == "original"

    def test_upsert_skips_empty_id(self):
        state = TodoState()
        state.upsert([{"id": "", "content": "no id"}])
        assert len(state.items) == 0

    def test_render_empty(self):
        state = TodoState()
        assert state.render() == "(no todos)"

    def test_render_status_markers(self):
        state = TodoState()
        state.items = [
            TodoItem(id="1", content="pending", status="pending"),
            TodoItem(id="2", content="progress", status="in_progress"),
            TodoItem(id="3", content="done", status="completed"),
            TodoItem(id="4", content="skip", status="cancelled"),
        ]
        rendered = state.render()
        assert "[ ]" in rendered
        assert "[>]" in rendered
        assert "[x]" in rendered
        assert "[~]" in rendered

    def test_upsert_merge_preserves_order(self):
        state = TodoState()
        state.upsert(
            [
                {"id": "a", "content": "first"},
                {"id": "b", "content": "second"},
            ]
        )
        state.upsert(
            [
                {"id": "c", "content": "third"},
                {"id": "a", "status": "completed"},
            ]
        )
        assert len(state.items) == 3
        assert state.items[0].id == "a"
        assert state.items[0].status == "completed"
        assert state.items[2].id == "c"


class TestTodoMiddleware:
    def test_has_write_todos_tool(self):
        mw = TodoListMiddleware()
        assert len(mw.tools) == 1
        assert mw.tools[0].name == "write_todos"

    def test_tool_creates_todos(self):
        mw = TodoListMiddleware()
        tool = mw.tools[0]
        result = tool.invoke(
            {
                "todos": [
                    {"id": "1", "content": "test task", "status": "pending"},
                ]
            }
        )
        assert "test task" in result

    def test_name_property(self):
        mw = TodoListMiddleware()
        assert mw.name == "TodoListMiddleware"

    def test_write_todos_syncs_task_runtime(self):
        task_runtime = TaskRuntimeService()
        mw = TodoListMiddleware(task_runtime=task_runtime)
        tool = mw.tools[0]

        tool.invoke(
            {
                "todos": [
                    {"id": "1", "content": "stabilize trunk", "status": "in_progress"},
                ]
            }
        )

        projection = task_runtime.build_projection()
        assert projection is not None
        assert projection["tasks"][0]["id"] == "1"
        assert projection["tasks"][0]["content"] == "stabilize trunk"


# ── Section 4: Bus Middleware Legacy ───────────────────────────────────

def test_bus_middleware_records_model_context_after_invoke():
    bus = MagicMock()
    middleware = BusMiddleware(bus)

    state = {"messages": [{"role": "user", "content": "hello"}]}
    middleware.before_invoke(state)
    middleware.after_invoke(state, {"response": "ok"})

    assert bus.share_context.call_count == 2
    first_call = bus.share_context.call_args_list[0]
    second_call = bus.share_context.call_args_list[1]
    assert first_call[0][0] == "last_invoke_duration_ms"
    assert second_call[0][0] == "last_model_call"
    assert second_call[0][1]["message_count"] == 1


def test_bus_middleware_wrap_tool_output_records_tool_invocation():
    bus = MagicMock()
    middleware = BusMiddleware(bus)

    output = middleware.wrap_tool_output("lookup", "result payload")

    assert output == "result payload"
    bus.record_invocation.assert_called_once()
    args = bus.record_invocation.call_args
    assert args[0][0] == "lookup"
    assert args[1]["success"] is True
    assert args[1]["source"] == "bus_middleware"
    assert args[1]["operation"] == "tool_output"


# ── Section 5: Event Bus ───────────────────────────────────────────────

class TestEventBusBasics:
    def setup_method(self):
        self.bus = EventBus()

    def test_subscribe_and_emit(self):
        received = []
        self.bus.subscribe(EventType.TOOL_CALL, lambda e: received.append(e))
        evt = Event(type=EventType.TOOL_CALL, payload={"tool": "calc"}, source="test")
        self.bus.emit(evt)
        assert len(received) == 1
        assert received[0].payload["tool"] == "calc"

    def test_subscribe_all_event_types_covers_fresh_enum_members(self):
        """Regression: SSE must subscribe to EventType enum, not event_bus._subs.keys()."""
        received: list[EventType] = []

        def handler(event: Event) -> None:
            received.append(event.type)

        for event_type in EventType:
            self.bus.subscribe(event_type, handler)

        for event_type in EventType:
            self.bus.emit(Event(type=event_type, source="test"))

        assert len(received) == len(EventType)
        assert set(received) == set(EventType)

    def test_unsubscribe(self):
        received = []

        def handler(event):
            received.append(event)

        self.bus.subscribe(EventType.TOOL_CALL, handler)
        assert self.bus.unsubscribe(EventType.TOOL_CALL, handler)
        self.bus.emit(Event(type=EventType.TOOL_CALL))
        assert len(received) == 0

    def test_unsubscribe_not_found(self):
        assert not self.bus.unsubscribe(EventType.TOOL_CALL, lambda e: None)

    def test_priority_ordering(self):
        order = []
        self.bus.subscribe(EventType.AGENT_START, lambda e: order.append("low"), priority=0)
        self.bus.subscribe(EventType.AGENT_START, lambda e: order.append("high"), priority=10)
        self.bus.subscribe(EventType.AGENT_START, lambda e: order.append("mid"), priority=5)
        self.bus.emit(Event(type=EventType.AGENT_START))
        assert order == ["high", "mid", "low"]

    def test_handler_isolation(self):
        received = []

        def bad_handler(e):
            raise RuntimeError("boom")

        self.bus.subscribe(EventType.ERROR, bad_handler, priority=10)
        self.bus.subscribe(EventType.ERROR, lambda e: received.append("ok"), priority=0)
        self.bus.emit(Event(type=EventType.ERROR))
        assert received == ["ok"]

    def test_event_type_filtering(self):
        tool_events = []
        agent_events = []
        self.bus.subscribe(EventType.TOOL_CALL, lambda e: tool_events.append(e))
        self.bus.subscribe(EventType.AGENT_START, lambda e: agent_events.append(e))
        self.bus.emit(Event(type=EventType.TOOL_CALL))
        self.bus.emit(Event(type=EventType.AGENT_START))
        self.bus.emit(Event(type=EventType.TOOL_CALL))
        assert len(tool_events) == 2
        assert len(agent_events) == 1


class TestEventBusHistory:
    def setup_method(self):
        self.bus = EventBus(history_limit=10)

    def test_history_records_events(self):
        self.bus.emit(Event(type=EventType.TOOL_CALL, payload={"n": 1}))
        self.bus.emit(Event(type=EventType.AGENT_START, payload={"n": 2}))
        h = self.bus.history()
        assert len(h) == 2

    def test_history_filter_by_type(self):
        self.bus.emit(Event(type=EventType.TOOL_CALL))
        self.bus.emit(Event(type=EventType.AGENT_START))
        self.bus.emit(Event(type=EventType.TOOL_CALL))
        h = self.bus.history(EventType.TOOL_CALL)
        assert len(h) == 2

    def test_history_limit(self):
        for i in range(20):
            self.bus.emit(Event(type=EventType.TOOL_CALL, payload={"i": i}))
        h = self.bus.history()
        assert len(h) == 10
        assert h[0].payload["i"] == 10

    def test_history_limit_param(self):
        for i in range(5):
            self.bus.emit(Event(type=EventType.TOOL_CALL, payload={"i": i}))
        h = self.bus.history(limit=2)
        assert len(h) == 2
        assert h[0].payload["i"] == 3


class TestEventBusClear:
    def test_clear_removes_subs_and_history(self):
        bus = EventBus()
        received = []
        bus.subscribe(EventType.TOOL_CALL, lambda e: received.append(e))
        bus.emit(Event(type=EventType.TOOL_CALL))
        assert len(received) == 1
        assert len(bus.history()) == 1
        bus.clear()
        assert bus.history() == []
        bus.emit(Event(type=EventType.TOOL_CALL))
        assert len(received) == 1  # handler removed, not called again
        assert len(bus.history()) == 1  # but event still recorded in history


class TestEventBusSubscriberCount:
    def test_count(self):
        bus = EventBus()
        bus.subscribe(EventType.TOOL_CALL, lambda e: None)
        bus.subscribe(EventType.TOOL_CALL, lambda e: None)
        bus.subscribe(EventType.AGENT_START, lambda e: None)
        assert bus.subscriber_count(EventType.TOOL_CALL) == 2
        assert bus.subscriber_count(EventType.AGENT_START) == 1
        assert bus.subscriber_count() == 3


class TestEventBusAsync:
    def test_emit_async(self):
        bus = EventBus()
        received = []

        async def async_handler(e):
            received.append(e.payload)

        bus.subscribe(EventType.TOOL_RESULT, async_handler)
        asyncio.run(bus.emit_async(Event(type=EventType.TOOL_RESULT, payload={"r": 42})))
        assert len(received) == 1
        assert received[0]["r"] == 42

    def test_emit_async_mixed_handlers(self):
        bus = EventBus()
        results = []

        async def ah(e):
            results.append("async")

        def sh(e):
            results.append("sync")

        bus.subscribe(EventType.MCP_CONNECT, ah, priority=5)
        bus.subscribe(EventType.MCP_CONNECT, sh, priority=0)
        asyncio.run(bus.emit_async(Event(type=EventType.MCP_CONNECT)))
        assert results == ["async", "sync"]


class TestEventBusConcurrency:
    def test_concurrent_emit(self):
        bus = EventBus()
        counter = {"n": 0}
        lock = threading.Lock()

        def handler(e):
            with lock:
                counter["n"] += 1

        bus.subscribe(EventType.COST_RECORD, handler)
        threads = []
        for _ in range(50):
            t = threading.Thread(target=lambda: bus.emit(Event(type=EventType.COST_RECORD)))
            threads.append(t)
            t.start()
        for t in threads:
            t.join()
        assert counter["n"] == 50
        assert len(bus.history(EventType.COST_RECORD)) == 50


class TestGlobalInstance:
    def test_global_event_bus_exists(self):
        assert isinstance(event_bus, EventBus)


class TestEventDataclass:
    def test_event_defaults(self):
        e = Event(type=EventType.ERROR)
        assert e.payload == {}
        assert e.source == ""
        assert e.session_id is None
        assert e.timestamp > 0

    def test_event_type_values(self):
        assert EventType.TOOL_CALL.value == "tool_call"
        assert EventType.GUARDRAIL_PASS.value == "guardrail_pass"


# ── Section 6: Hooks Runtime ───────────────────────────────────────────

def test_hooks_runtime_tightens_permission_verdict_in_plan_mode():
    runtime = create_default_hooks_runtime()

    result = runtime.run_phase(
        HookPhase.PERMISSION_DECISION,
        {
            "tool_name": "write_file",
            "projected_runtime_view": {
                "permission": {"mode": "plan"},
                "settings": {"permission_mode": "plan"},
            },
        },
    )

    assert result["verdict"] == "deny"
    assert "plan mode blocks mutations" in " ".join(result["reason_fragments"])


def test_hooks_runtime_biases_route_back_to_trunk_when_compacted():
    runtime = create_default_hooks_runtime()

    result = runtime.run_phase(
        HookPhase.ROUTE_SELECTION,
        {
            "projected_runtime_view": {
                "permission": {"mode": "plan"},
                "context_hygiene": {"summary_active": True},
                "isolation": {"multi_agent_ready": False},
            }
        },
    )

    assert result["force_trunk_first"] is True
    assert "tool_runtime_governance" in result["prefer_slots"]
    assert "subagent_runtime" in result["avoid_slots"]


# ── Section 7: Patch Tool Calls ────────────────────────────────────────

@pytest.mark.skipif(not _HAS_LC, reason="langchain not installed")
class TestPatchStaticMethod:
    def test_empty_messages_returns_none(self):
        assert PatchToolCallsMiddleware._patch([]) is None

    def test_no_dangling_returns_none(self):
        ai = _ai_with_tool_calls([{"id": "tc1", "name": "foo", "args": {}}])
        tool_msg = ToolMessage(content="ok", tool_call_id="tc1")
        assert PatchToolCallsMiddleware._patch([ai, tool_msg]) is None

    def test_single_dangling_gets_patched(self):
        ai = _ai_with_tool_calls([{"id": "tc1", "name": "bar", "args": {}}])
        human = HumanMessage(content="new message")
        result = PatchToolCallsMiddleware._patch([ai, human])
        assert result is not None
        assert len(result) == 3
        assert isinstance(result[1], ToolMessage)
        assert result[1].tool_call_id == "tc1"
        assert "cancelled" in result[1].content

    def test_multiple_dangling_calls(self):
        ai = _ai_with_tool_calls(
            [
                {"id": "tc1", "name": "a", "args": {}},
                {"id": "tc2", "name": "b", "args": {}},
            ]
        )
        result = PatchToolCallsMiddleware._patch([ai])
        assert result is not None
        patched_tool_msgs = [m for m in result if isinstance(m, ToolMessage)]
        assert len(patched_tool_msgs) == 2
        ids = {m.tool_call_id for m in patched_tool_msgs}
        assert ids == {"tc1", "tc2"}

    def test_partial_dangling(self):
        ai = _ai_with_tool_calls(
            [
                {"id": "tc1", "name": "a", "args": {}},
                {"id": "tc2", "name": "b", "args": {}},
            ]
        )
        tool_msg = ToolMessage(content="ok", tool_call_id="tc1")
        result = PatchToolCallsMiddleware._patch([ai, tool_msg])
        assert result is not None
        patched = [m for m in result if isinstance(m, ToolMessage) and "cancelled" in m.content]
        assert len(patched) == 1
        assert patched[0].tool_call_id == "tc2"

    def test_ai_without_tool_calls_ignored(self):
        msgs = [AIMessage(content="hello"), HumanMessage(content="hi")]
        assert PatchToolCallsMiddleware._patch(msgs) is None

    def test_wrap_model_call_patches_request(self):
        ai = _ai_with_tool_calls([{"id": "tc1", "name": "x", "args": {}}])
        request = MagicMock()
        request.messages = [ai, HumanMessage(content="y")]
        captured = {}

        def handler(req):
            captured["messages"] = list(req.messages)
            return "response"

        request.override = lambda messages: MagicMock(messages=messages)
        mw = PatchToolCallsMiddleware()
        mw.wrap_model_call(request, handler)
        assert len(captured["messages"]) == 3


@pytest.mark.skipif(not _HAS_LC, reason="langchain not installed")
class TestPatchEdgeCases:
    def test_tool_call_with_empty_id(self):
        ai = _ai_with_tool_calls([{"id": "", "name": "z", "args": {}}])
        result = PatchToolCallsMiddleware._patch([ai])
        assert result is not None
        patched = [m for m in result if isinstance(m, ToolMessage)]
        assert len(patched) == 1

    def test_interleaved_ai_messages(self):
        ai1 = _ai_with_tool_calls([{"id": "tc1", "name": "a", "args": {}}])
        tool1 = ToolMessage(content="ok", tool_call_id="tc1")
        ai2 = _ai_with_tool_calls([{"id": "tc2", "name": "b", "args": {}}])
        result = PatchToolCallsMiddleware._patch([ai1, tool1, ai2])
        assert result is not None
        patched = [m for m in result if isinstance(m, ToolMessage) and "cancelled" in m.content]
        assert len(patched) == 1
        assert patched[0].tool_call_id == "tc2"


def _ai_with_tool_calls(tool_calls: list[dict]) -> AIMessage:
    if not _HAS_LC:
        raise RuntimeError("LangChain not installed")
    return AIMessage(content="", tool_calls=tool_calls)
