from __future__ import annotations

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from core.systems.middleware.summarization_middleware import SummarizationConfig
from core.systems.runtime.context_hygiene_runtime import ContextHygieneRuntime
from core.systems.runtime.hooks_runtime import HookPhase, HooksRuntime


def _human(text: str) -> HumanMessage:
    return HumanMessage(content=text)


def _ai(text: str, tool_calls: list | None = None) -> AIMessage:
    return AIMessage(content=text, tool_calls=tool_calls or [])


def test_context_hygiene_runtime_records_history_snip_and_projection(tmp_path):
    runtime = ContextHygieneRuntime(
        config=SummarizationConfig(keep_recent_messages=2, offload_dir=str(tmp_path), thread_id="thread-1")
    )
    messages = [_human(f"msg {index}") for index in range(6)]
    payloads: list[dict] = []

    count = runtime.summarize(
        messages,
        summarize_fn=lambda prompt: "summary text",
        resume_bundle_text="## Task Runtime\n- t1: stabilize",
        compaction_callback=payloads.append,
    )

    assert count == 4
    assert runtime.summary_message is not None
    assert "### Post-compact rebuild" in runtime.summary_message.content
    assert payloads[-1]["summary"] == "### Post-compact rebuild\n### Resume bundle\n## Task Runtime\n- t1: stabilize\n\nsummary text"
    assert payloads[-1]["history_snip_count"] == 1 or payloads[-1]["metadata"]["history_snip_count"] == 1

    projection = runtime.build_projection()
    assert projection["summary_active"] is True
    assert projection["history_snip_count"] == 1
    assert projection["latest_boundary"]["summary"].startswith("### Post-compact rebuild")


def test_context_hygiene_runtime_microcompact_preserves_tool_preview():
    runtime = ContextHygieneRuntime(config=SummarizationConfig(keep_recent_messages=2, microcompact_age=2))
    messages = [
        _ai("", tool_calls=[{"id": "tc-read", "name": "read_file", "args": {"path": "src/app.py"}}]),
        ToolMessage(content=("line\n" * 300), tool_call_id="tc-read", name="read_file"),
        _human("step 1"),
        _human("step 2"),
        _human("step 3"),
    ]

    compacted = runtime.microcompact(messages)

    assert compacted[1].content.startswith("[microcompact]")
    assert "read_file(path=src/app.py)" in compacted[1].content


def test_context_hygiene_runtime_runs_fixed_rebuild_and_writeback_hooks(tmp_path):
    hooks = HooksRuntime()
    hooks.register(
        HookPhase.CONTEXT_HYGIENE_REBUILD,
        "rebuild_guard",
        lambda payload: {
            "prepend_sections": ["### Runtime Guardrail\nRebuild strictly from the projected runtime view."],
        },
    )
    hooks.register(
        HookPhase.CONTEXT_HYGIENE_WRITEBACK,
        "writeback_tags",
        lambda payload: {
            "notes": ["compaction checkpoint captured"],
            "session_tags": ["compact:recorded"],
            "boundary_annotations": {"owner": "context_hygiene"},
        },
    )
    runtime = ContextHygieneRuntime(
        config=SummarizationConfig(keep_recent_messages=2, offload_dir=str(tmp_path), thread_id="thread-hooks"),
        hooks_runtime=hooks,
    )
    payloads: list[dict] = []

    runtime.summarize(
        [_human(f"msg {index}") for index in range(5)],
        summarize_fn=lambda prompt: "summary text",
        resume_bundle_text="## Task Runtime\n- t1: stabilize",
        projected_runtime_view={"context_hygiene": {"summary_active": True}},
        compaction_callback=payloads.append,
    )

    assert payloads
    assert "Rebuild strictly from the projected runtime view." in payloads[-1]["summary"]
    assert payloads[-1]["metadata"]["hook_notes"] == ["compaction checkpoint captured"]
    assert payloads[-1]["metadata"]["hook_session_tags"] == ["compact:recorded"]
    assert payloads[-1]["metadata"]["boundary_annotations"]["owner"] == "context_hygiene"
