"""Tests for Multi-Dimensional Session Runtime & Projected Views.

Consolidates:
1. test_session_runtime.py
2. test_session_runtime_view.py
3. test_projected_runtime_view.py
4. test_workspace_view_service.py
5. test_asset_branch_surfaces.py
"""

from __future__ import annotations

import json

import pytest

# Imports for Section 1: Session Runtime
from core.systems.integration import GatewayRuntime
from core.systems.session import SessionRuntime
from core.systems.runtime.event_bus import Event, EventBus, EventType

# Imports for Section 2: Session Runtime View
from core.systems.context.projected_runtime_view import build_projected_runtime_view, build_runtime_task_section
from core.systems.session.session_runtime_view import (
    compile_runtime_resume_view,
    merge_session_runtime_view,
    render_resume_dict_context,
)

# Imports for Section 3: Projected Runtime View
from core.systems.context.projected_runtime_view import (
    merge_projected_runtime_views,
    render_projected_runtime_view,
)

# Imports for Section 4: Workspace View Service
from core.assets.tools import get_file_system_tools
from core.systems.context import WorkspaceViewService

# Imports for Section 5: Asset Branch Surfaces
from core.systems.apps import app_branch, app_modes, app_orchestration, app_runtime
from core.assets.workflows import workflow_branch, workflow_collaboration, workflow_orchestration, workflow_runtime


# ── Section 1: Session Runtime ──────────────────────────────────────────

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
    kernel = runtime.get_kernel_snapshot("thread-1")
    assert stored is not None
    assert kernel is not None
    assert stored["message_count"] == 2
    assert stored["last_user_message"] == "hello world"
    assert stored["last_assistant_message"] == "hi there"
    assert stored["working_summary"] == "User is greeting the assistant"
    assert "runtime_view" not in stored["metadata"]
    assert stored["runtime_view"]["hooks"]["notes"] == ["Keep responses concise"]
    assert stored["runtime_view"]["session"]["summary"] == "User is greeting the assistant"
    assert kernel["runtime_view"]["system_context"]["thread_id"] == "thread-1"
    assert kernel["runtime_view"]["system_context"]["working_summary"] == "User is greeting the assistant"
    assert kernel["runtime_view"]["session"]["session_notebook_summary"] == "Keep responses concise"
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

    entries = stored["runtime_view"]["workspace"]["entries"]
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
    assert stored["runtime_view"]["context_hygiene"]["compacted_timeline_events"] >= 1
    assert stored["runtime_view"]["context_hygiene"]["notebook_entries"] >= 1
    assert "compaction" in stored["runtime_view"]["session"]
    assert "notebook" in stored["runtime_view"]["session"]


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
    assert stored["runtime_view"]["context_hygiene"]["resume_scrubbed_events"] >= 1
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
    assert stored["runtime_view"]["workspace"]["entries"][-1]["memory_type"] == "user"


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
    assert stored["runtime_view"]["context_hygiene"]["boundaries"]
    assert stored["runtime_view"]["context_hygiene"]["boundaries"][-1]["source"] == "middleware.summarization"


def test_session_runtime_keeps_working_summary_out_of_session_notebook(temp_paths):
    runtime = SessionRuntime(temp_paths.sessions_file)
    runtime.ensure_thread_session("thread-summary-only", title="Summary only")
    runtime.update_summary("thread-summary-only", summary="Canonical runtime view owns this working summary")

    stored = runtime.get_session("thread-summary-only")
    artifacts = runtime.get_compiled_runtime_view("thread-summary-only")
    kernel = runtime.get_kernel_snapshot("thread-summary-only")

    assert stored is not None
    assert artifacts is not None
    assert kernel is not None
    assert stored["runtime_view"]["system_context"]["working_summary"] == "Canonical runtime view owns this working summary"
    assert stored["runtime_view"]["session"]["session_notebook_summary"] == ""
    assert artifacts["projected_runtime_view"]["session"]["session_notebook_summary"] == ""
    assert kernel["runtime_view"]["session"]["session_notebook_summary"] == ""


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
    assert stored["runtime_view"]["context_hygiene"]["compacted_tool_events"] >= 1
    assert stored["runtime_view"]["context_hygiene"]["compacted_file_views"] >= 1
    assert stored["runtime_view"]["context_hygiene"]["tool_notebook_entries"] >= 1
    assert stored["runtime_view"]["context_hygiene"]["file_view_notebook_entries"] >= 1
    assert stored["runtime_view"]["session"]["tool_transcript"]["entries"]
    assert stored["runtime_view"]["workspace"]["file_view_notebook"]["entries"]
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

    first = runtime.get_compiled_runtime_view("thread-artifacts")
    second = runtime.get_compiled_runtime_view("thread-artifacts")

    assert first is not None
    assert second is not None
    assert first["artifact_version"] >= 1
    assert second["artifact_version"] == first["artifact_version"]
    assert first["projected_runtime_view"]["workspace"]["recent_views"][-1]["view_kind"] == "partial"
    assert first["projected_runtime_view"]["workspace"]["view_hashes"]

    runtime.set_prompt_injection("thread-artifacts", prompt_injection="Prefer terse operational summaries.")
    after = runtime.get_compiled_runtime_view("thread-artifacts")
    kernel = runtime.get_kernel_snapshot("thread-artifacts")

    assert after is not None
    assert kernel is not None
    assert after["artifact_version"] > first["artifact_version"]
    assert after["system_context"]["prompt_injection"] == "Prefer terse operational summaries."
    assert kernel["runtime_view"]["system_context"]["prompt_injection"] == "Prefer terse operational summaries."


def test_session_runtime_persists_runtime_view_for_resume(temp_paths):
    runtime = SessionRuntime(temp_paths.sessions_file)
    runtime.ensure_thread_session("thread-bookkeeping", title="Bookkeeping")

    runtime.update_runtime_view(
        thread_id="thread-bookkeeping",
        source="runtime.artifacts",
        projected_runtime_view={
            "settings": {"summary": "trusted settings: user -> project", "permission_mode": "plan"},
            "context_hygiene": {"summary_active": True, "history_snip_count": 2},
            "hooks": {"summary": "5 hooks across 3 active phases", "session_tags": ["permission:plan"]},
            "route": {"recommended": {"slot": "workspace_view", "top_level": "workspace_view"}},
            "isolation": {"summary": "root runtime uses the project workspace", "multi_agent_ready": True},
        },
    )

    artifacts = runtime.get_compiled_runtime_view("thread-bookkeeping")
    assert artifacts is not None
    assert artifacts["system_context"]["thread_id"] == "thread-bookkeeping"
    assert artifacts["projected_runtime_view"]["settings"]["permission_mode"] == "plan"
    assert artifacts["projected_runtime_view"]["context_hygiene"]["history_snip_count"] == 2
    assert artifacts["projected_runtime_view"]["hooks"]["session_tags"] == ["permission:plan"]
    assert artifacts["projected_runtime_view"]["route"]["recommended"]["slot"] == "workspace_view"
    assert artifacts["projected_runtime_view"]["isolation"]["multi_agent_ready"] is True

    restored = SessionRuntime(temp_paths.sessions_file)
    restored_artifacts = restored.get_compiled_runtime_view("thread-bookkeeping")
    assert restored_artifacts is not None
    assert restored_artifacts["projected_runtime_view"]["settings"]["permission_mode"] == "plan"
    assert restored_artifacts["projected_runtime_view"]["route"]["recommended"]["slot"] == "workspace_view"


def test_session_runtime_rehydrates_canonical_bookkeeping_from_event_log(temp_paths):
    runtime = SessionRuntime(temp_paths.sessions_file)
    runtime.ensure_thread_session("thread-canonical", title="Canonical")
    runtime.record_message(thread_id="thread-canonical", role="user", content="summarize current progress")
    runtime.remember("thread-canonical", note="Keep summaries operator-facing")
    runtime.record_file_view(
        thread_id="thread-canonical",
        path="/repo/plan.md",
        preview="# plan\n- tighten canonical bookkeeping\n",
        offset=0,
        limit=120,
        is_partial_view=True,
    )

    restored = SessionRuntime(temp_paths.sessions_file)
    kernel = restored.get_kernel_snapshot("thread-canonical")
    artifacts = restored.get_compiled_runtime_view("thread-canonical")

    assert kernel is not None
    assert artifacts is not None
    assert kernel["runtime_view"]["system_context"]["thread_id"] == "thread-canonical"
    assert kernel["runtime_view"]["session"]["session_notebook_summary"] == "Keep summaries operator-facing"
    assert kernel["runtime_view"]["workspace"]["recent_views"][-1]["path"] == "/repo/plan.md"
    assert artifacts["projected_runtime_view"]["workspace"]["recent_views"][-1]["path"] == "/repo/plan.md"


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


def test_session_recorder_invariants():
    from core.systems.session.session_record import SessionRecord

    # 1. Event Ordering Invariant
    record = SessionRecord(
        session_key="session-inv",
        thread_id="thread-inv",
    )
    record.timeline.append({"timestamp": 10.0, "kind": "chat", "title": "First"})
    record.timeline.append({"timestamp": 5.0, "kind": "chat", "title": "Second"})  # out of order
    record.updated_at = 15.0

    with pytest.raises(ValueError, match="Event ordering invariant violated"):
        record.verify_invariants()

    # Fix order
    record.timeline[1]["timestamp"] = 20.0
    record.updated_at = 15.0  # now updated_at < last event

    # 2. Replay Safety Invariant
    with pytest.raises(ValueError, match="Replay safety invariant violated"):
        record.verify_invariants()

    # Fix replay safety
    record.updated_at = 25.0
    record.verify_invariants()  # Should pass now

    # 3. Compaction Boundaries Invariant
    record.timeline.append({"timestamp": 30.0, "kind": "compaction", "title": "Compaction"})
    record.updated_at = 35.0
    record.metadata["compiled_artifacts"] = {"context_hygiene": {"history_snip_count": 0}}

    with pytest.raises(ValueError, match="Compaction boundary invariant violated"):
        record.verify_invariants()

    record.metadata["compiled_artifacts"]["context_hygiene"]["history_snip_count"] = 1
    record.verify_invariants()  # Should pass now


# ── Section 2: Session Runtime View ─────────────────────────────────────

def test_compile_runtime_resume_view_renders_workspace_and_notes():
    artifacts = compile_runtime_resume_view(
        build_projected_runtime_view(
            thread_id="thread-1",
            root_mode="assistant",
            system_context={
                "latest_compaction_boundary": {
                    "source": "middleware.summarization",
                    "reason": "conversation_compaction",
                    "summary": "Summarized earlier tool transcript",
                    "source_event_range": {"message_count": 12},
                    "retained_recent_window": {"recent_window_messages": 20},
                }
            },
            session={
                "session_notebook_summary": "## Current objective\nStabilize trunk",
                "compaction_summary": "Summarized earlier tool transcript",
            },
            workspace={
                "recent_paths": ["/repo/app.py"],
                "recent_views": [
                    {
                        "path": "/repo/app.py",
                        "view_kind": "partial",
                        "is_partial_view": True,
                        "view_hash": "vh-1",
                    }
                ],
            },
            tasks=build_runtime_task_section(
                task_projection={
                    "summary": "1 active task",
                    "items": [{"id": "t1", "content": "stabilize trunk", "status": "in_progress"}],
                },
                recent_tool_runs=[
                    {"title": "read_file", "status": "completed", "source": "tool_control", "run_id": "run-1"}
                ],
                permission={
                    "mode": "plan",
                    "summary": "mode=plan, 1 active rule",
                    "rules": [
                        {"tool_name": "read_file", "verdict": "ask", "reason": "manual review", "source": "session"}
                    ],
                    "recent_events": [{"action": "set_mode", "mode": "plan", "timestamp": 1.0}],
                },
                latest_compaction_boundary={
                    "summary": "Summarized earlier tool transcript",
                    "source": "middleware.summarization",
                },
            ),
            permission={
                "mode": "plan",
                "summary": "mode=plan, 1 active rule",
                "rules": [
                    {"tool_name": "read_file", "verdict": "ask", "reason": "manual review", "source": "session"}
                ],
                "recent_events": [{"action": "set_mode", "mode": "plan", "timestamp": 1.0}],
            },
            settings={
                "summary": "trusted settings: system -> user -> project, permission=plan",
                "active_sources": ["system", "user", "project"],
                "sources": [
                    {"source": "user", "path": "/runtime/config.json", "entry_count": 4},
                    {"source": "project", "path": "/repo/.pybot/project.config.json", "entry_count": 1},
                ],
            },
            capability={
                "trunk_summary": "Tool Runtime / Governance -> Workspace View -> Context Hygiene",
                "execution_summary": "Single-Agent Runtime builds on the trunk; Skills stay as a strategy overlay.",
                "primary_branches": [
                    {
                        "label": "Workflow / Apps / Automation",
                        "depends_on": ["Single-Agent Runtime", "Permission / Recovery"],
                        "children": ["App Asset Runtime", "App Modes"],
                        "capabilities": ["create_app", "build_app_iteratively"],
                    }
                ],
                "secondary_branches": ["Hooks Runtime", "UI Overlay / Review Surface"],
                "route_hints": [
                    {
                        "topic": "create_app",
                        "hint": "Prefer build_app_iteratively first, then create_app -> update_app_file -> verify_app -> test_app_api.",
                    }
                ],
            },
            hooks={
                "summary": "5 hooks across 3 active phases",
                "phase_counts": [{"phase": "route_selection", "handler_count": 2}],
                "session_tags": ["permission:plan"],
            },
            route={
                "recommended": {
                    "slot": "workspace_view",
                    "slot_label": "Workspace View",
                    "top_level": "workspace_view",
                    "top_level_label": "Workspace View",
                    "summary": "Stay on the trunk through Workspace View first.",
                },
                "notes": ["plan mode biases routing toward analysis and trunk capabilities"],
            },
            isolation={
                "summary": "root runtime uses the project workspace; subagent isolation chain is available",
                "visibility": "project",
                "adapter": "workspace",
                "multi_agent_ready": True,
                "delegation_ready": True,
            },
        )
    )

    assert artifacts is not None
    assert artifacts["system_context"]["thread_id"] == "thread-1"
    view = artifacts["projected_runtime_view"]
    assert view["session"]["session_notebook_summary"] == "## Current objective\nStabilize trunk"
    assert view["session"]["compaction_summary"] == "Summarized earlier tool transcript"
    assert view["workspace"]["recent_paths"] == ["/repo/app.py"]
    assert view["workspace"]["view_hashes"] == ["vh-1"]
    assert view["tasks"]["tasks"][0]["id"] == "t1"
    assert view["tasks"]["activities"][0]["kind"] == "tool_run"
    assert view["permission"]["mode"] == "plan"
    assert view["permission"]["rules"][0]["tool_name"] == "read_file"
    assert view["settings"]["active_sources"] == ["system", "user", "project"]
    assert view["capability"]["primary_branches"][0]["label"] == "Workflow / Apps / Automation"
    assert view["hooks"]["session_tags"] == ["permission:plan"]
    assert view["route"]["recommended"]["slot"] == "workspace_view"
    assert view["isolation"]["visibility"] == "project"
    assert view["context_hygiene"]["summary_active"] is True

    rendered = render_resume_dict_context(artifacts)
    assert "## Session Notebook" in rendered
    assert "Stabilize trunk" in rendered
    assert "## Task Runtime" in rendered
    assert "## Recent Activity" in rendered
    assert "## Workspace Views" in rendered
    assert "## Permission Control" in rendered
    assert "## Trusted Settings" in rendered
    assert "mode: plan" in rendered
    assert "## Latest Compaction" in rendered
    assert "/repo/app.py" in rendered
    assert "Capability Tree" in rendered
    assert "route:create_app" in rendered
    assert "## Context Hygiene" in rendered
    assert "## Hooks Runtime" in rendered
    assert "## Route Selection" in rendered
    assert "## Isolation Model" in rendered


def test_merge_session_runtime_view_augments_base_with_live_workspace_projection():
    base = {
        "system_context": {"thread_id": "thread-1", "primary_mode": "assistant"},
        "projected_runtime_view": {
            "session": {"session_notebook_summary": ""},
            "workspace": {
                "recent_paths": ["/repo/old.py"],
                "recent_views": [{"path": "/repo/old.py", "view_hash": "old"}],
                "view_hashes": ["old"],
                "partial_views": 0,
                "path_labels": ["old.py"],
            },
        },
    }
    overlay = {
        "system_context": {"primary_mode": "admin"},
        "projected_runtime_view": {
            "session": {"session_notebook_summary": "live notes"},
            "workspace": {
                "recent_paths": ["/repo/app.py"],
                "recent_views": [{"path": "/repo/app.py", "view_hash": "new", "is_partial_view": True}],
            },
        },
    }

    merged = merge_session_runtime_view(base, overlay)

    assert merged is not None
    assert merged["system_context"]["primary_mode"] == "admin"
    assert merged["projected_runtime_view"]["session"]["session_notebook_summary"] == "live notes"
    assert merged["projected_runtime_view"]["workspace"]["recent_paths"] == ["/repo/old.py", "/repo/app.py"]
    assert merged["projected_runtime_view"]["workspace"]["view_hashes"] == ["old", "new"]
    assert merged["projected_runtime_view"]["workspace"]["partial_views"] == 1


def test_render_and_merge_session_runtime_view_include_tasks_and_recent_actions():
    base = {
        "projected_runtime_view": {
            "tasks": {
                "tasks": [{"id": "t1", "content": "stabilize trunk", "status": "in_progress"}],
                "activities": [{"activity_id": "run-1", "kind": "tool_run", "title": "read_file", "status": "completed"}],
                "summary": "1 active tasks, 0 completed, 1 recent activities",
            }
        }
    }
    overlay = {
        "projected_runtime_view": {
            "tasks": {
                "tasks": [{"id": "t2", "content": "wire resume bundle", "status": "pending"}],
                "activities": [{"activity_id": "run-2", "kind": "tool_run", "title": "write_file", "status": "completed"}],
                "summary": "2 active tasks, 0 completed, 2 recent activities",
            }
        }
    }

    merged = merge_session_runtime_view(base, overlay)

    assert merged is not None
    task_runtime = merged["projected_runtime_view"]["tasks"]
    assert [item["id"] for item in task_runtime["tasks"]] == ["t1", "t2"]
    assert [item["title"] for item in task_runtime["activities"] if item["kind"] == "tool_run"] == [
        "read_file",
        "write_file",
    ]

    rendered = render_resume_dict_context(merged)
    assert "## Task Runtime" in rendered
    assert "t1: stabilize trunk" in rendered
    assert "t2: wire resume bundle" in rendered
    assert "## Recent Activity" in rendered
    assert "read_file" in rendered
    assert "write_file" in rendered


def test_merge_session_runtime_view_preserves_permission_and_compaction_context():
    base = {
        "system_context": {
            "thread_id": "thread-1",
            "primary_mode": "assistant",
            "latest_compaction_boundary": {
                "reason": "conversation_compaction",
                "source": "middleware.summarization",
                "summary": "base summary",
            },
        },
        "projected_runtime_view": {
            "permission": {
                "mode": "default",
                "summary": "mode=default, 1 active rule",
                "rules": [{"tool_name": "bash", "verdict": "ask", "reason": "guard", "source": "session"}],
                "recent_events": [{"action": "set_rule", "tool_name": "bash", "timestamp": 1.0}],
            }
        },
    }
    overlay = {
        "system_context": {
            "latest_compaction_boundary": {
                "reason": "conversation_compaction",
                "source": "session_runtime",
                "summary": "overlay summary",
                "source_event_range": {"message_count": 8},
            }
        },
        "projected_runtime_view": {
            "permission": {
                "mode": "plan",
                "summary": "mode=plan, 2 active rules",
                "rules": [{"tool_name": "read_file", "verdict": "allow", "reason": "inspect", "source": "user"}],
                "recent_events": [{"action": "set_mode", "mode": "plan", "timestamp": 2.0}],
            }
        },
    }

    merged = merge_session_runtime_view(base, overlay)

    assert merged is not None
    assert merged["projected_runtime_view"]["permission"]["mode"] == "plan"
    assert [item["tool_name"] for item in merged["projected_runtime_view"]["permission"]["rules"]] == ["bash", "read_file"]
    assert [item["action"] for item in merged["projected_runtime_view"]["permission"]["recent_events"]] == ["set_rule", "set_mode"]
    assert merged["system_context"]["latest_compaction_boundary"]["summary"] == "overlay summary"


def test_merge_session_runtime_view_merges_capability_projection_and_route_hints():
    base = {
        "projected_runtime_view": {
            "capability": {
                "trunk_summary": "Tool Runtime / Governance -> Workspace View",
                "route_hints": [{"topic": "multi_agent", "hint": "delegate only when separable"}],
            }
        }
    }
    overlay = {
        "projected_runtime_view": {
            "capability": {
                "execution_summary": "Single-Agent Runtime builds on the trunk.",
                "route_hints": [{"topic": "create_app", "hint": "prefer build_app_iteratively"}],
            }
        }
    }

    merged = merge_session_runtime_view(base, overlay)

    assert merged is not None
    assert merged["projected_runtime_view"]["capability"]["trunk_summary"] == "Tool Runtime / Governance -> Workspace View"
    assert merged["projected_runtime_view"]["capability"]["execution_summary"] == "Single-Agent Runtime builds on the trunk."
    assert [item["topic"] for item in merged["projected_runtime_view"]["capability"]["route_hints"]] == [
        "multi_agent",
        "create_app",
    ]


def test_merge_session_runtime_view_builds_task_runtime_from_projection_inputs():
    merged = merge_session_runtime_view(
        compile_runtime_resume_view(
            build_projected_runtime_view(
                thread_id="thread-1",
                root_mode="assistant",
                tasks=build_runtime_task_section(
                    task_projection={"items": [{"id": "t1", "content": "inspect tree", "status": "completed"}]},
                    recent_tool_runs=[{"title": "read_file", "run_id": "run-1", "status": "completed"}],
                ),
            )
        ),
        compile_runtime_resume_view(
            build_projected_runtime_view(
                thread_id="thread-1",
                root_mode="assistant",
                permission={"recent_events": [{"action": "set_mode", "mode": "plan", "timestamp": 2.0}]},
                tasks=build_runtime_task_section(
                    permission={"recent_events": [{"action": "set_mode", "mode": "plan", "timestamp": 2.0}]},
                    latest_compaction_boundary={
                        "reason": "conversation_compaction",
                        "summary": "compacted",
                        "timestamp": 3.0,
                    },
                ),
                system_context={
                    "latest_compaction_boundary": {
                        "reason": "conversation_compaction",
                        "summary": "compacted",
                        "timestamp": 3.0,
                    }
                },
                session={"compaction_summary": "compacted"},
            )
        ),
    )

    assert merged is not None
    projection = merged["projected_runtime_view"]["tasks"]
    assert [item["id"] for item in projection["tasks"]] == ["t1"]
    assert [item["kind"] for item in projection["activities"]] == ["tool_run", "governance", "compaction"]


# ── Section 3: Projected Runtime View ───────────────────────────────────

def test_projected_runtime_view_compiles_canonical_sections():
    view = build_projected_runtime_view(
        thread_id="thread-1",
        root_mode="assistant",
        session={"session_notebook_summary": "Continue the refactor"},
        workspace={
            "recent_views": [{"path": "/repo/app.py", "view_kind": "partial", "view_hash": "vh-1"}],
        },
        tasks=build_runtime_task_section(
            task_projection={
                "items": [{"id": "t1", "content": "stabilize trunk", "status": "in_progress"}],
            },
            recent_tool_runs=[
                {"title": "read_file", "run_id": "run-1", "status": "completed"},
            ],
            permission={
                "mode": "plan",
                "rules": [{"tool_name": "read_file", "verdict": "allow", "source": "session"}],
            },
        ),
        permission={
            "mode": "plan",
            "rules": [{"tool_name": "read_file", "verdict": "allow", "source": "session"}],
        },
        settings={
            "summary": "trusted settings: system -> user -> session, permission=plan",
            "active_sources": ["system", "user", "session"],
            "sources": [{"source": "user", "path": "/runtime/config.json", "entry_count": 3}],
        },
    )

    artifacts = view.to_resume_dict()

    view_payload = artifacts["projected_runtime_view"]
    assert view_payload["session"]["session_notebook_summary"] == "Continue the refactor"
    assert view_payload["workspace"]["recent_paths"] == ["/repo/app.py"]
    assert view_payload["tasks"]["tasks"][0]["id"] == "t1"
    assert view_payload["tasks"]["activities"][0]["title"] == "read_file"
    assert view_payload["permission"]["mode"] == "plan"
    assert view_payload["settings"]["active_sources"] == ["system", "user", "session"]
    assert view_payload["context_hygiene"]["history_snip_count"] == 0


def test_projected_runtime_view_merge_prefers_overlay_and_keeps_history():
    base = build_projected_runtime_view(
        thread_id="thread-1",
        root_mode="assistant",
        session={"session_notebook_summary": "base notes"},
        tasks=build_runtime_task_section(
            task_projection={"items": [{"id": "t1", "content": "inspect", "status": "completed"}]},
            recent_tool_runs=[{"title": "read_file", "run_id": "run-1", "status": "completed"}],
        ),
    )
    overlay = build_projected_runtime_view(
        thread_id="thread-1",
        root_mode="admin",
        session={"session_notebook_summary": "overlay notes"},
        tasks=build_runtime_task_section(
            task_projection={"items": [{"id": "t2", "content": "rewrite", "status": "in_progress"}]},
            recent_tool_runs=[{"title": "write_file", "run_id": "run-2", "status": "completed"}],
        ),
        permission={"mode": "bypass"},
        settings={"summary": "trusted settings: system -> project", "active_sources": ["system", "project"]},
    )

    merged = merge_projected_runtime_views(base, overlay)
    assert merged is not None

    artifacts = merged.to_resume_dict()
    assert artifacts["system_context"]["primary_mode"] == "admin"
    assert artifacts["projected_runtime_view"]["session"]["session_notebook_summary"] == "overlay notes"
    assert [item["id"] for item in artifacts["projected_runtime_view"]["tasks"]["tasks"]] == ["t1", "t2"]
    assert [item["title"] for item in artifacts["projected_runtime_view"]["tasks"]["activities"] if item["kind"] == "tool_run"] == [
        "read_file",
        "write_file",
    ]
    assert artifacts["projected_runtime_view"]["permission"]["mode"] == "bypass"
    assert artifacts["projected_runtime_view"]["settings"]["active_sources"] == ["system", "project"]

    rendered = render_projected_runtime_view(artifacts["projected_runtime_view"])
    assert "## Session Notebook" in rendered
    assert "## Task Runtime" in rendered
    assert "## Recent Activity" in rendered
    assert "## Trusted Settings" in rendered


def test_projected_runtime_view_renders_context_hygiene_from_latest_boundary():
    view = build_projected_runtime_view(
        thread_id="thread-1",
        root_mode="assistant",
        system_context={
            "latest_compaction_boundary": {
                "summary": "compacted",
                "metadata": {"microcompact_count": 2},
            }
        },
        session={"compaction_summary": "compacted"},
    )

    artifacts = view.to_resume_dict()

    assert artifacts["projected_runtime_view"]["context_hygiene"]["summary_active"] is True
    assert artifacts["projected_runtime_view"]["context_hygiene"]["history_snip_count"] == 1

    rendered = render_projected_runtime_view(artifacts["projected_runtime_view"])
    assert "## Context Hygiene" in rendered
    assert "history snips: 1" in rendered


def test_projected_runtime_view_includes_hooks_route_isolation_and_team_memory_sections():
    view = build_projected_runtime_view(
        thread_id="thread-1",
        root_mode="assistant",
        hooks={
            "summary": "5 hooks across 3 active phases",
            "phase_counts": [{"phase": "route_selection", "handler_count": 2}],
            "session_tags": ["permission:plan"],
        },
        route={
            "recommended": {
                "slot": "workspace_view",
                "slot_label": "Workspace View",
                "top_level": "workspace_view",
                "top_level_label": "Workspace View",
                "summary": "Stay on the trunk through Workspace View first.",
            },
            "notes": ["plan mode biases routing toward analysis and trunk capabilities"],
        },
        isolation={
            "summary": "root runtime uses the project workspace; subagent isolation chain is available",
            "visibility": "project",
            "adapter": "workspace",
            "multi_agent_ready": True,
        },
        team_memory={
            "summary": "team memory for session-1: 1 active run, 2 recent notes",
            "team_key": "session-1",
            "participant_agents": ["worker"],
            "recent_notes": [
                {"note_id": "n1", "agent_name": "worker", "note": "checked failing tests"},
            ],
            "shared_memory_ready": True,
        },
    )

    artifacts = view.to_resume_dict()

    assert artifacts["projected_runtime_view"]["hooks"]["phase_counts"][0]["phase"] == "route_selection"
    assert artifacts["projected_runtime_view"]["route"]["recommended"]["slot"] == "workspace_view"
    assert artifacts["projected_runtime_view"]["isolation"]["visibility"] == "project"
    assert artifacts["projected_runtime_view"]["team_memory"]["team_key"] == "session-1"

    rendered = render_projected_runtime_view(artifacts["projected_runtime_view"])
    assert "## Hooks Runtime" in rendered
    assert "## Route Selection" in rendered
    assert "## Isolation Model" in rendered
    assert "## Team Memory" in rendered


# ── Section 4: Workspace View Service ───────────────────────────────────

def _tool_by_name(tools, name: str):
    for tool in tools:
        if tool.name == name:
            return tool
    raise AssertionError(f"Tool not found: {name}")


def test_workspace_view_deduplicates_full_and_exact_partial_ranges(tmp_path):
    target = tmp_path / "app.py"
    target.write_text("alpha\nbeta\ngamma\n", encoding="utf-8")

    workspace_view = WorkspaceViewService()
    tools = get_file_system_tools(
        allowed_root=str(tmp_path),
        workspace_view=workspace_view,
    )
    read_tool = _tool_by_name(tools, "read_file")

    assert read_tool._run("app.py") == "alpha\nbeta\ngamma\n"

    full_stub = read_tool._run("app.py")
    assert "[FILE_UNCHANGED]" in full_stub
    assert "视图: full" in full_stub

    partial_view = read_tool._run("app.py", offset=1, limit=1)
    assert partial_view.startswith("[partial view] 行 2-2/3 of app.py")
    assert partial_view.endswith("beta\n")

    partial_stub = read_tool._run("app.py", offset=1, limit=1)
    assert "[FILE_UNCHANGED]" in partial_stub
    assert "视图: partial 2-2/3" in partial_stub

    different_partial = read_tool._run("app.py", offset=0, limit=1)
    assert different_partial.startswith("[partial view] 行 1-1/3 of app.py")
    assert different_partial.endswith("alpha\n")

    full_stub_again = read_tool._run("app.py")
    assert "[FILE_UNCHANGED]" in full_stub_again
    assert "视图: full" in full_stub_again

    projection = workspace_view.build_projection(limit=8)
    assert projection["recent_paths"] == [str(target)]
    assert projection["recent_views"][-1]["path"] == str(target)
    assert projection["recent_views"][-1]["view_kind"] == "partial"
    assert projection["partial_views"] >= 1
    assert projection["path_labels"] == ["app.py"]

    assert workspace_view.stats["full_hits"] >= 2
    assert workspace_view.stats["partial_hits"] >= 1


def test_workspace_view_invalidates_on_write_and_replace(tmp_path):
    target = tmp_path / "app.py"
    target.write_text("first\nsecond\n", encoding="utf-8")

    workspace_view = WorkspaceViewService()
    tools = get_file_system_tools(
        allowed_root=str(tmp_path),
        workspace_view=workspace_view,
    )
    read_tool = _tool_by_name(tools, "read_file")
    write_tool = _tool_by_name(tools, "write_file")
    replace_tool = _tool_by_name(tools, "str_replace")

    assert read_tool._run("app.py") == "first\nsecond\n"
    assert "[FILE_UNCHANGED]" in read_tool._run("app.py")

    assert "成功写入文件" in write_tool._run("app.py", "updated\nbody\n")
    assert read_tool._run("app.py") == "updated\nbody\n"

    assert read_tool._run("app.py", offset=0, limit=1).startswith("[partial view] 行 1-1/2 of app.py")
    assert "成功替换文件" in replace_tool._run("app.py", "updated", "fresh")
    assert read_tool._run("app.py") == "fresh\nbody\n"

    assert workspace_view.stats["invalidations"] >= 2


# ── Section 5: Asset Branch Surfaces ───────────────────────────────────

def test_workflow_branch_surfaces_are_grouped_by_layer():
    assert workflow_branch.runtime is workflow_runtime
    assert workflow_branch.collaboration is workflow_collaboration
    assert workflow_branch.orchestration is workflow_orchestration
    assert workflow_runtime.engine_class.__name__ == "PyFlowEngine"
    assert workflow_collaboration.runtime_class.__name__ == "WorkflowCollaborationRuntime"
    assert workflow_orchestration.scheduler_class.__name__ == "TaskScheduler"


def test_workflow_runtime_surface_exports_extended_node_operators():
    assert callable(workflow_runtime.run_http_request)
    assert callable(workflow_runtime.run_question_classifier)
    assert callable(workflow_runtime.run_database_query)
    assert callable(workflow_runtime.run_file_write)


def test_app_branch_surfaces_are_grouped_by_layer():
    assert app_branch.runtime is app_runtime
    assert app_branch.modes is app_modes
    assert app_branch.orchestration is app_orchestration
    assert app_runtime.manager_class.__name__ == "AppManager"
    assert app_modes.matrix_runtime_class.__name__ == "AppMatrixRuntime"
    assert app_orchestration.registry_class.__name__ == "AppOrchestrationRegistry"


def test_app_runtime_surface_keeps_managed_entrypoints():
    tool_names = {tool.name for tool in app_runtime.creator_tools_factory()}
    assert "create_app" in tool_names
    assert "update_app_file" in tool_names
    assert "test_app_api" in tool_names
