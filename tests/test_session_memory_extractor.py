from __future__ import annotations

import time

from core.systems.memory.session_memory_extractor import SessionMemoryExtractor
from core.systems.context.workspace_view import WorkspaceViewService


class _Msg:
    def __init__(self, type_: str, content: str = "", tool_calls: list[dict] | None = None):
        self.type = type_
        self.content = content
        self.tool_calls = tool_calls or []


def test_build_conversation_text_includes_ai_tool_calls():
    messages = [
        _Msg("human", "Please update app.py"),
        _Msg(
            "ai",
            "",
            tool_calls=[
                {"id": "tc1", "name": "read_file", "args": {"path": "app.py"}},
                {"id": "tc2", "name": "write_file", "args": {"path": "app.py"}},
            ],
        ),
        _Msg("tool", "done"),
    ]

    text = SessionMemoryExtractor._build_conversation_text(messages)

    assert "Please update app.py" in text
    assert "Assistant tool calls" in text
    assert "read_file" in text
    assert "write_file" in text
    assert "app.py" in text


def test_session_memory_fallback_includes_workspace_view_paths(tmp_path):
    workspace_view = WorkspaceViewService()
    target = tmp_path / "app.py"
    workspace_view.record_view(
        resolved_path=str(target),
        content="print('hello')\n",
        mtime=1.0,
        file_size=15,
    )

    extractor = SessionMemoryExtractor(workspace_view=workspace_view)
    messages = [
        _Msg("human", "Please continue the refactor"),
        _Msg("ai", "I will inspect the workspace view next."),
    ]

    assert extractor.force_extract(messages)
    notes = extractor.get_notes() or ""

    assert "Please continue the refactor" in notes
    assert str(target) in notes
    assert "Files touched" in notes


def test_session_memory_scheduler_triggers_on_natural_pause_and_recovers_from_stale_run():
    extractor = SessionMemoryExtractor()
    extractor._config.min_messages_for_extraction = 2
    extractor._config.natural_pause_interval = 2
    messages = [
        _Msg("human", "Keep going"),
        _Msg("ai", "I will continue."),
    ]

    assert extractor.tick(messages, tool_call_delta=0, current_token_count=0) is False
    assert extractor.tick(messages, tool_call_delta=0, current_token_count=0) is True
    assert extractor.get_scheduler_state()["last_reason"] == "natural_pause"

    extractor._extract_status = "running"
    extractor._extract_started_at = time.time() - 60

    assert extractor.force_extract(messages) is True
    assert extractor.get_scheduler_state()["status"] == "idle"
