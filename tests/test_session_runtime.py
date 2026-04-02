from __future__ import annotations

import json

from core.systems.integration import GatewayRuntime
from core.systems.runtime import SessionRuntime
from core.systems.runtime.event_bus import Event, EventBus, EventType


def test_session_runtime_tracks_messages_and_memory(temp_paths):
    runtime = SessionRuntime(temp_paths.sessions_file)

    created = runtime.ensure_thread_session("thread-1", title="Demo session")
    assert created["session_key"] == "thread-1"
    assert created["primary_mode"] == "assistant"

    runtime.record_message(thread_id="thread-1", role="user", content="hello world")
    runtime.record_message(thread_id="thread-1", role="assistant", content="hi there")
    runtime.update_summary("thread-1", summary="User is greeting the assistant")
    runtime.remember("thread-1", note="Keep responses concise")

    stored = runtime.get_session("thread-1")
    assert stored is not None
    assert stored["message_count"] == 2
    assert stored["last_user_message"] == "hello world"
    assert stored["last_assistant_message"] == "hi there"
    assert stored["working_summary"] == "User is greeting the assistant"
    assert stored["context_notes"] == ["Keep responses concise"]
    assert stored["memory_layers"]["session"]["summary"] == "User is greeting the assistant"
    events = runtime.get_event_log("thread-1")
    assert any(item["op"] == "message_recorded" for item in events)


def test_session_runtime_enforces_typed_durable_memory(temp_paths):
    runtime = SessionRuntime(temp_paths.sessions_file)
    runtime.ensure_thread_session("thread-memory", title="Memory typing")

    stored = runtime.remember(
        "thread-memory",
        note="User prefers short status pings",
        layer="workspace",
        memory_type="user",
        durable=True,
        verified=True,
    )

    entries = stored["memory_layers"]["workspace"]["entries"]
    assert entries[-1]["memory_type"] == "user"
    assert entries[-1]["durable"] is True

    try:
        runtime.remember(
            "thread-memory",
            note="The repository has workflow_node_runtime.py",
            layer="workspace",
            memory_type="project",
            durable=True,
            occurred_on="2026-04-02",
        )
    except ValueError as exc:
        assert "derivable from repository facts" in str(exc)
    else:
        raise AssertionError("expected repository-derived durable memory to be rejected")


def test_session_runtime_syncs_gateway_sessions_and_runs(temp_paths):
    gateway = GatewayRuntime(temp_paths.workspace_data_dir)
    gateway.sessions.touch(
        "gw-session",
        mode="admin",
        thread_id="gateway-admin-gw-session",
        source="http.responses",
        user="ops",
        device_id="device-1",
        client_id="client-1",
    )
    gateway.runs.start(
        run_id="run-1",
        response_id="resp-1",
        session_key="gw-session",
        thread_id="gateway-admin-gw-session",
        mode="admin",
        requested_model="pybot:admin",
        source="http.responses",
        display_input="diagnose",
    )
    gateway.runs.complete("run-1", output_text="done")

    runtime = SessionRuntime(temp_paths.sessions_file)
    runtime.sync_gateway_runtime(gateway)
    runtime.sync_gateway_runtime(gateway)

    stored = runtime.get_session("gw-session")
    assert stored is not None
    assert stored["thread_id"] == "gateway-admin-gw-session"
    assert stored["primary_mode"] == "admin"
    assert stored["gateway"]["user"] == "ops"
    assert stored["gateway"]["device_ids"] == ["device-1"]
    assert stored["latest_run"]["run_id"] == "run-1"
    assert stored["latest_run"]["status"] == "completed"
    assert [item["kind"] for item in stored["timeline"]] == ["gateway_run"]
    assert stored["timeline"][0]["run_id"] == "run-1"
    assert stored["timeline"][0]["status"] == "completed"


def test_session_runtime_builds_timeline_and_compacts_context(temp_paths):
    bus = EventBus()
    runtime = SessionRuntime(
        temp_paths.sessions_file,
        max_timeline_events=2,
        max_context_notes=1,
        max_note_chars=24,
        event_bus=bus,
    )
    runtime.ensure_thread_session("thread-compact", title="Compact me")

    bus.emit(
        Event(
            type=EventType.TOOL_CALL,
            session_id="thread-compact",
            source="Tool:search_docs",
            payload={"tool_name": "search_docs", "status": "started", "thread_id": "thread-compact"},
        )
    )
    bus.emit(
        Event(
            type=EventType.SCHEDULE_RUN,
            session_id="thread-compact",
            source="PersistentAdminRuntime",
            payload={
                "run_kind": "durable_task",
                "task_id": "task-1",
                "task_name": "Synthesize docs",
                "task_status": "running",
                "step_description": "Inspect telemetry",
                "root_mode": "admin",
            },
        )
    )
    bus.emit(
        Event(
            type=EventType.SUBAGENT_COMPLETED,
            session_id="thread-compact",
            source="subagent_registry",
            payload={
                "agent_name": "researcher",
                "status": "completed",
                "run_id": "subagent-1",
                "last_response": "Done",
                "thread_id": "thread-compact",
            },
        )
    )

    runtime.remember("thread-compact", note="first note")
    runtime.remember("thread-compact", note="second note that should force compaction")

    stored = runtime.get_session("thread-compact")
    assert stored is not None
    assert len(stored["timeline"]) == 2
    assert stored["timeline"][-1]["kind"] == "delegated_subagent"
    assert stored["compaction_state"]["compacted_timeline_events"] >= 1
    assert stored["compaction_state"]["notebook_entries"] >= 1
    assert "compaction" in stored["memory_layers"]["session"]
    assert "notebook" in stored["memory_layers"]["session"]


def test_session_runtime_syncs_workflow_runs_into_session_overview(temp_paths):
    from core.assets.workflows.models import WorkflowRunRecord

    class DummyExecutionRuntime:
        @property
        def run_history(self):
            return [
                WorkflowRunRecord(
                    run_id="wf-run-1",
                    workflow_id="wf-1",
                    workflow_name="Nightly ETL",
                    thread_id="thread-workflow",
                    session_key="session-workflow",
                    root_mode="admin",
                    source="workflow.trigger",
                    status="completed",
                    completed_nodes=3,
                    total_nodes=3,
                )
            ]

    class DummyEngine:
        execution_runtime = DummyExecutionRuntime()

    runtime = SessionRuntime(temp_paths.sessions_file)
    runtime.sync_workflow_runtime(DummyEngine())

    stored = runtime.get_session("session-workflow")
    assert stored is not None
    assert stored["timeline"][-1]["kind"] == "workflow_run"
    assert stored["timeline"][-1]["run_id"] == "wf-run-1"

    overview = runtime.get_overview("session-workflow")
    assert overview is not None
    assert overview["counts"]["by_kind"]["workflow_run"] == 1
    assert overview["latest_by_kind"]["workflow_run"]["status"] == "completed"


def test_session_runtime_resume_scrubber_repairs_incomplete_runs(temp_paths):
    runtime = SessionRuntime(temp_paths.sessions_file)
    runtime.ensure_thread_session("thread-resume", title="Resume me")
    runtime.add_timeline_event(
        thread_id="thread-resume",
        kind="tool_run",
        title="search_docs",
        status="running",
        source="test",
        preview="streaming tool result",
    )
    runtime.record_run(
        session_key="thread-resume",
        thread_id="thread-resume",
        run_id="run-resume",
        mode="assistant",
        status="in_progress",
        source="gateway",
        display_input="continue",
    )

    restored = SessionRuntime(temp_paths.sessions_file)
    stored = restored.get_session("thread-resume")
    assert stored is not None
    assert stored["latest_run"]["status"] == "interrupted"
    assert stored["timeline"][-1]["status"] == "interrupted"
    assert stored["compaction_state"]["resume_scrubbed_events"] >= 1
    events = restored.get_event_log("thread-resume", op="resume_scrubbed")
    assert events


def test_session_runtime_replays_from_event_log_even_if_snapshot_is_stale(temp_paths):
    runtime = SessionRuntime(temp_paths.sessions_file)
    runtime.ensure_thread_session("thread-ledger", title="Ledger first")
    runtime.record_message(thread_id="thread-ledger", role="user", content="hello ledger")
    runtime.update_summary("thread-ledger", summary="Ledger summary")
    runtime.remember(
        "thread-ledger",
        note="User prefers ledger-backed sessions",
        layer="workspace",
        memory_type="user",
        durable=True,
        verified=True,
    )

    temp_paths.sessions_file.write_text("{}", encoding="utf-8")

    restored = SessionRuntime(temp_paths.sessions_file)
    stored = restored.get_session("thread-ledger")
    assert stored is not None
    assert stored["message_count"] == 1
    assert stored["working_summary"] == "Ledger summary"
    assert stored["memory_layers"]["workspace"]["entries"][-1]["memory_type"] == "user"


def test_session_runtime_ignores_snapshot_without_event_ledger(temp_paths):
    temp_paths.sessions_file.parent.mkdir(parents=True, exist_ok=True)
    temp_paths.sessions_file.write_text(
        json.dumps(
            {
                "sessions": {
                    "legacy-session": {
                        "session_key": "legacy-session",
                        "thread_id": "legacy-thread",
                        "primary_mode": "assistant",
                        "title": "legacy snapshot",
                    }
                }
            }
        ),
        encoding="utf-8",
    )

    restored = SessionRuntime(temp_paths.sessions_file)

    assert restored.list_sessions() == []


def test_session_runtime_records_external_compaction_boundaries(temp_paths):
    runtime = SessionRuntime(temp_paths.sessions_file)
    runtime.ensure_thread_session("thread-compact-external", title="External compaction")

    stored = runtime.record_external_compaction(
        thread_id="thread-compact-external",
        summary="Conversation summarized into a compact artifact",
        message_count=8,
        recent_window=4,
        source="middleware.summarization",
    )

    assert stored["timeline"][-1]["kind"] == "conversation_compaction"
    assert stored["compaction_state"]["boundaries"]
    assert stored["compaction_state"]["boundaries"][-1]["source"] == "middleware.summarization"


def test_session_runtime_compacts_tool_transcript_and_file_views(temp_paths):
    bus = EventBus()
    runtime = SessionRuntime(
        temp_paths.sessions_file,
        max_timeline_events=24,
        event_bus=bus,
    )
    runtime.ensure_thread_session("thread-tools", title="Tool-heavy transcript")

    for index in range(10):
        bus.emit(
            Event(
                type=EventType.TOOL_RESULT,
                session_id="thread-tools",
                source="Tool:read_file",
                payload={
                    "tool_name": "read_file",
                    "status": "completed",
                    "thread_id": "thread-tools",
                    "args": {"path": f"/repo/src/file_{index}.py", "offset": index * 10, "limit": 240},
                    "preview": "x" * 320,
                },
            )
        )

    runtime.compact_session("thread-tools", reason="manual")
    stored = runtime.get_session("thread-tools")

    assert stored is not None
    assert stored["compaction_state"]["compacted_tool_events"] >= 1
    assert stored["compaction_state"]["compacted_file_views"] >= 1
    assert stored["compaction_state"]["tool_notebook_entries"] >= 1
    assert stored["compaction_state"]["file_view_notebook_entries"] >= 1
    assert stored["memory_layers"]["session"]["tool_transcript"]["entries"]
    assert stored["memory_layers"]["workspace"]["file_views"]["notebook"]["entries"]
    assert any(item["kind"] == "file_view" for item in stored["timeline"])


def test_session_runtime_compiled_artifacts_invalidate_with_prompt_injection(temp_paths):
    runtime = SessionRuntime(temp_paths.sessions_file)
    runtime.ensure_thread_session("thread-artifacts", title="Artifacts")
    runtime.record_message(thread_id="thread-artifacts", role="user", content="please summarize the repo")
    runtime.record_file_view(
        thread_id="thread-artifacts",
        path="/repo/main.py",
        preview="def main():\n    return 1\n",
        offset=10,
        limit=80,
        is_partial_view=True,
    )

    first = runtime.get_compiled_artifacts("thread-artifacts")
    second = runtime.get_compiled_artifacts("thread-artifacts")

    assert first is not None
    assert second is not None
    assert first["artifact_version"] >= 1
    assert second["artifact_version"] == first["artifact_version"]
    assert first["file_view_projection"]["recent_views"][-1]["view_kind"] == "partial"
    assert first["file_view_projection"]["view_hashes"]

    runtime.set_prompt_injection("thread-artifacts", prompt_injection="Prefer terse operational summaries.")
    after = runtime.get_compiled_artifacts("thread-artifacts")
    kernel = runtime.get_kernel_snapshot("thread-artifacts")

    assert after is not None
    assert kernel is not None
    assert after["artifact_version"] > first["artifact_version"]
    assert after["system_context"]["prompt_injection"] == "Prefer terse operational summaries."
    assert kernel["mutable_artifacts"]["prompt_injection"] == "Prefer terse operational summaries."


def test_session_runtime_rebuilds_checkpoint_and_tracks_sidechains(temp_paths):
    bus = EventBus()
    runtime = SessionRuntime(temp_paths.sessions_file, max_timeline_events=12, event_bus=bus)
    runtime.ensure_thread_session("thread-sidechains", title="Sidechains")

    runtime.remember(
        "thread-sidechains",
        note="User prefers deployment updates with absolute dates",
        layer="workspace",
        memory_type="user",
        durable=True,
        verified=True,
    )
    for index in range(9):
        bus.emit(
            Event(
                type=EventType.TOOL_RESULT,
                session_id="thread-sidechains",
                source="Tool:read_file",
                payload={
                    "tool_name": "read_file",
                    "status": "completed",
                    "thread_id": "thread-sidechains",
                    "args": {"path": f"/repo/file_{index}.py", "offset": index * 5, "limit": 200},
                    "preview": "print('x')" * 40,
                },
            )
        )

    runtime.compact_session("thread-sidechains", reason="manual")
    checkpoint = runtime.rebuild_checkpoint()
    sidechains = runtime.get_sidechains("thread-sidechains")
    saved = json.loads(temp_paths.sessions_file.read_text(encoding="utf-8"))
    file_views = runtime.get_file_views("thread-sidechains")

    assert checkpoint["session_count"] == 1
    assert "thread-sidechains" in saved["sessions"]
    assert any(item["purpose"] == "memory_extraction" for item in sidechains)
    assert any(item["purpose"] == "tool_result_distillation" for item in sidechains)
    assert any(item["purpose"] == "context_compaction" for item in sidechains)
    assert file_views[-1]["content_hash"]
    assert file_views[-1]["view_hash"]
