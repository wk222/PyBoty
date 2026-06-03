from __future__ import annotations

from core.systems.runtime.projected_runtime_view import build_projected_runtime_view, build_runtime_task_section
from core.systems.runtime.session.session_runtime_view import (
    compile_runtime_resume_view,
    merge_session_runtime_view,
    render_resume_dict_context,
)


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
