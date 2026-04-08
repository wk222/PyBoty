from __future__ import annotations

from core.systems.runtime.projected_runtime_view import (
    build_runtime_task_section,
    build_projected_runtime_view,
    merge_projected_runtime_views,
    render_projected_runtime_view,
)


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
