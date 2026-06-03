"""Deep merge of two :class:`ProjectedRuntimeView` instances/payloads."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .projected_runtime_view import ProjectedRuntimeView


def _as_text(value: Any) -> str:
    return str(value or "").strip()


def _merge_system_context(base: dict[str, Any], overlay: dict[str, Any]) -> dict[str, Any]:
    merged = dict(base)
    for key, value in overlay.items():
        if isinstance(value, dict) and not value:
            continue
        if value in ("", None, []):
            continue
        merged[key] = value
    return merged


def merge_projected_runtime_views(
    base: "dict[str, Any] | ProjectedRuntimeView | None",
    overlay: "dict[str, Any] | ProjectedRuntimeView | None",
) -> "ProjectedRuntimeView | None":
    """Merge two views; ``overlay`` wins for scalar fields, lists are concatenated."""
    from .projected_runtime_view import (
        ProjectedRuntimeView,
        _dedupe_mappings,
        _merge_notebook_block,
        _normalize_capability,
        _normalize_context_hygiene,
        _normalize_hooks,
        _normalize_isolation,
        _normalize_permission,
        _normalize_route,
        _normalize_session,
        _normalize_settings,
        _normalize_task_runtime,
        _normalize_team_memory,
        _normalize_workspace_view,
        extract_projected_runtime_view,
    )

    base_view = extract_projected_runtime_view(base)
    overlay_view = extract_projected_runtime_view(overlay)
    if base_view is None:
        return overlay_view
    if overlay_view is None:
        return base_view

    merged_system = _merge_system_context(base_view.system_context, overlay_view.system_context)
    merged_session = _normalize_session(
        {
            "session_notebook_summary": _as_text(overlay_view.session.get("session_notebook_summary"))
            or _as_text(base_view.session.get("session_notebook_summary")),
            "working_summary": _as_text(overlay_view.session.get("working_summary"))
            or _as_text(base_view.session.get("working_summary")),
            "compaction_summary": _as_text(overlay_view.session.get("compaction_summary"))
            or _as_text(base_view.session.get("compaction_summary")),
            "notebook": _merge_notebook_block(
                base_view.session.get("notebook", {}),
                overlay_view.session.get("notebook", {}),
                limit=12,
            ),
            "tool_transcript": _merge_notebook_block(
                base_view.session.get("tool_transcript", {}),
                overlay_view.session.get("tool_transcript", {}),
                limit=16,
            ),
            "compaction": dict(base_view.session.get("compaction", {}))
            | dict(overlay_view.session.get("compaction", {})),
            "summary": _as_text(overlay_view.session.get("summary"))
            or _as_text(base_view.session.get("summary")),
            "last_note": _as_text(overlay_view.session.get("last_note"))
            or _as_text(base_view.session.get("last_note")),
            "last_note_type": _as_text(overlay_view.session.get("last_note_type"))
            or _as_text(base_view.session.get("last_note_type")),
            "entries": list(base_view.session.get("entries", []))
            + list(overlay_view.session.get("entries", [])),
        },
        working_summary=(
            _as_text(overlay_view.session.get("working_summary"))
            or _as_text(base_view.session.get("working_summary"))
        ),
        compaction_summary=(
            _as_text(overlay_view.session.get("compaction_summary"))
            or _as_text(base_view.session.get("compaction_summary"))
        ),
    )
    merged_workspace = _normalize_workspace_view(
        {
            "recent_paths": list(base_view.workspace.get("recent_paths", []))
            + list(overlay_view.workspace.get("recent_paths", [])),
            "recent_views": list(base_view.workspace.get("recent_views", []))
            + list(overlay_view.workspace.get("recent_views", [])),
            "notebook_summary": _as_text(overlay_view.workspace.get("notebook_summary"))
            or _as_text(base_view.workspace.get("notebook_summary")),
            "stats": dict(base_view.workspace.get("stats", {}))
            | dict(overlay_view.workspace.get("stats", {})),
            "file_view_notebook": _merge_notebook_block(
                base_view.workspace.get("file_view_notebook", {}),
                overlay_view.workspace.get("file_view_notebook", {}),
                limit=16,
            ),
            "last_updated_at": overlay_view.workspace.get("last_updated_at")
            or base_view.workspace.get("last_updated_at"),
            "summary": _as_text(overlay_view.workspace.get("summary"))
            or _as_text(base_view.workspace.get("summary")),
            "last_note": _as_text(overlay_view.workspace.get("last_note"))
            or _as_text(base_view.workspace.get("last_note")),
            "last_note_type": _as_text(overlay_view.workspace.get("last_note_type"))
            or _as_text(base_view.workspace.get("last_note_type")),
            "entries": list(base_view.workspace.get("entries", []))
            + list(overlay_view.workspace.get("entries", [])),
        }
    )
    overlay_permission_mode = _as_text(overlay_view.permission.get("mode"))
    overlay_has_permission_data = bool(
        overlay_view.permission.get("rules")
        or overlay_view.permission.get("recent_events")
        or overlay_view.permission.get("write_tools")
        or overlay_permission_mode not in ("", "default")
        or _as_text(overlay_view.permission.get("summary"))
    )
    merged_permission = _normalize_permission(
        {
            "mode": (
                overlay_permission_mode
                if overlay_has_permission_data
                else _as_text(base_view.permission.get("mode"))
            ),
            "summary": _as_text(overlay_view.permission.get("summary"))
            or _as_text(base_view.permission.get("summary")),
            "rules": list(base_view.permission.get("rules", []))
            + list(overlay_view.permission.get("rules", [])),
            "recent_events": list(base_view.permission.get("recent_events", []))
            + list(overlay_view.permission.get("recent_events", [])),
            "write_tools": list(base_view.permission.get("write_tools", []))
            + list(overlay_view.permission.get("write_tools", [])),
            "sources": dict(base_view.permission.get("sources", {}))
            | dict(overlay_view.permission.get("sources", {})),
        }
    )
    overlay_settings_mode = _as_text(overlay_view.settings.get("permission_mode"))
    overlay_has_settings_data = bool(
        overlay_view.settings.get("active_sources")
        or overlay_view.settings.get("sources")
        or overlay_view.settings.get("paths")
        or overlay_settings_mode not in ("", "default")
        or _as_text(overlay_view.settings.get("summary"))
    )
    merged_settings = _normalize_settings(
        {
            "summary": _as_text(overlay_view.settings.get("summary"))
            or _as_text(base_view.settings.get("summary")),
            "active_sources": list(base_view.settings.get("active_sources", []))
            + list(overlay_view.settings.get("active_sources", [])),
            "sources": list(base_view.settings.get("sources", []))
            + list(overlay_view.settings.get("sources", [])),
            "paths": dict(base_view.settings.get("paths", {}))
            | dict(overlay_view.settings.get("paths", {})),
            "permission_mode": (
                overlay_settings_mode
                if overlay_has_settings_data
                else _as_text(base_view.settings.get("permission_mode"))
            ),
        }
    )
    merged_tasks = _normalize_task_runtime(
        {
            "summary": _as_text(overlay_view.tasks.get("summary"))
            or _as_text(base_view.tasks.get("summary")),
            "tasks": list(base_view.tasks.get("tasks", [])) + list(overlay_view.tasks.get("tasks", [])),
            "activities": list(base_view.tasks.get("activities", []))
            + list(overlay_view.tasks.get("activities", [])),
        },
        permission_projection=merged_permission,
        latest_compaction_boundary=merged_system.get("latest_compaction_boundary", {}),
    )
    merged_capability = _normalize_capability(
        {
            "trunk_chain": list(base_view.capability.get("trunk_chain", []))
            + list(overlay_view.capability.get("trunk_chain", [])),
            "trunk_summary": _as_text(overlay_view.capability.get("trunk_summary"))
            or _as_text(base_view.capability.get("trunk_summary")),
            "execution_surfaces": list(base_view.capability.get("execution_surfaces", []))
            + list(overlay_view.capability.get("execution_surfaces", [])),
            "execution_summary": _as_text(overlay_view.capability.get("execution_summary"))
            or _as_text(base_view.capability.get("execution_summary")),
            "primary_branches": list(base_view.capability.get("primary_branches", []))
            + list(overlay_view.capability.get("primary_branches", [])),
            "secondary_branches": list(base_view.capability.get("secondary_branches", []))
            + list(overlay_view.capability.get("secondary_branches", [])),
            "principles": list(base_view.capability.get("principles", []))
            + list(overlay_view.capability.get("principles", [])),
            "route_hints": list(base_view.capability.get("route_hints", []))
            + list(overlay_view.capability.get("route_hints", [])),
        }
    )
    merged_context_hygiene = _normalize_context_hygiene(
        {
            "summary_active": bool(base_view.context_hygiene.get("summary_active"))
            or bool(overlay_view.context_hygiene.get("summary_active")),
            "current_cutoff_index": max(
                int(base_view.context_hygiene.get("current_cutoff_index", 0) or 0),
                int(overlay_view.context_hygiene.get("current_cutoff_index", 0) or 0),
            ),
            "last_microcompact_count": int(
                overlay_view.context_hygiene.get("last_microcompact_count", 0) or 0
            )
            or int(base_view.context_hygiene.get("last_microcompact_count", 0) or 0),
            "history_snip_count": max(
                int(base_view.context_hygiene.get("history_snip_count", 0) or 0),
                int(overlay_view.context_hygiene.get("history_snip_count", 0) or 0),
            ),
            "latest_boundary": (
                dict(overlay_view.context_hygiene.get("latest_boundary", {}))
                if isinstance(overlay_view.context_hygiene.get("latest_boundary"), dict)
                and overlay_view.context_hygiene.get("latest_boundary")
                else dict(base_view.context_hygiene.get("latest_boundary", {}))
            ),
            "boundaries": _dedupe_mappings(
                list(base_view.context_hygiene.get("boundaries", []))
                + list(overlay_view.context_hygiene.get("boundaries", [])),
                key_fields=("boundary_id", "timestamp", "reason"),
                limit=16,
            ),
            "history": _dedupe_mappings(
                list(base_view.context_hygiene.get("history", []))
                + list(overlay_view.context_hygiene.get("history", [])),
                key_fields=("timestamp", "reason", "summary"),
                limit=12,
            ),
            "resume_scrubbed_events": max(
                int(base_view.context_hygiene.get("resume_scrubbed_events", 0) or 0),
                int(overlay_view.context_hygiene.get("resume_scrubbed_events", 0) or 0),
            ),
            "compacted_tool_events": max(
                int(base_view.context_hygiene.get("compacted_tool_events", 0) or 0),
                int(overlay_view.context_hygiene.get("compacted_tool_events", 0) or 0),
            ),
            "tool_notebook_entries": max(
                int(base_view.context_hygiene.get("tool_notebook_entries", 0) or 0),
                int(overlay_view.context_hygiene.get("tool_notebook_entries", 0) or 0),
            ),
            "compacted_file_views": max(
                int(base_view.context_hygiene.get("compacted_file_views", 0) or 0),
                int(overlay_view.context_hygiene.get("compacted_file_views", 0) or 0),
            ),
            "file_view_notebook_entries": max(
                int(base_view.context_hygiene.get("file_view_notebook_entries", 0) or 0),
                int(overlay_view.context_hygiene.get("file_view_notebook_entries", 0) or 0),
            ),
            "compacted_notes": max(
                int(base_view.context_hygiene.get("compacted_notes", 0) or 0),
                int(overlay_view.context_hygiene.get("compacted_notes", 0) or 0),
            ),
            "compacted_timeline_events": max(
                int(base_view.context_hygiene.get("compacted_timeline_events", 0) or 0),
                int(overlay_view.context_hygiene.get("compacted_timeline_events", 0) or 0),
            ),
            "microcompacted_previews": max(
                int(base_view.context_hygiene.get("microcompacted_previews", 0) or 0),
                int(overlay_view.context_hygiene.get("microcompacted_previews", 0) or 0),
            ),
            "microcompacted_metadata": max(
                int(base_view.context_hygiene.get("microcompacted_metadata", 0) or 0),
                int(overlay_view.context_hygiene.get("microcompacted_metadata", 0) or 0),
            ),
            "notebook_entries": max(
                int(base_view.context_hygiene.get("notebook_entries", 0) or 0),
                int(overlay_view.context_hygiene.get("notebook_entries", 0) or 0),
            ),
            "last_reason": _as_text(overlay_view.context_hygiene.get("last_reason"))
            or _as_text(base_view.context_hygiene.get("last_reason")),
            "last_compacted_at": overlay_view.context_hygiene.get("last_compacted_at")
            or base_view.context_hygiene.get("last_compacted_at"),
        }
    )
    merged_hooks = _normalize_hooks(
        {
            "summary": _as_text(overlay_view.hooks.get("summary"))
            or _as_text(base_view.hooks.get("summary")),
            "active_phases": list(base_view.hooks.get("active_phases", []))
            + list(overlay_view.hooks.get("active_phases", [])),
            "phase_counts": list(base_view.hooks.get("phase_counts", []))
            + list(overlay_view.hooks.get("phase_counts", [])),
            "recent_runs": list(base_view.hooks.get("recent_runs", []))
            + list(overlay_view.hooks.get("recent_runs", [])),
            "notes": list(base_view.hooks.get("notes", [])) + list(overlay_view.hooks.get("notes", [])),
            "session_tags": list(base_view.hooks.get("session_tags", []))
            + list(overlay_view.hooks.get("session_tags", [])),
        }
    )
    merged_route = _normalize_route(
        {
            "summary": _as_text(overlay_view.route.get("summary"))
            or _as_text(base_view.route.get("summary")),
            "recommended": dict(base_view.route.get("recommended", {}))
            | dict(overlay_view.route.get("recommended", {})),
            "prefer_slots": list(base_view.route.get("prefer_slots", []))
            + list(overlay_view.route.get("prefer_slots", [])),
            "avoid_slots": list(base_view.route.get("avoid_slots", []))
            + list(overlay_view.route.get("avoid_slots", [])),
            "avoid_top_levels": list(base_view.route.get("avoid_top_levels", []))
            + list(overlay_view.route.get("avoid_top_levels", [])),
            "force_trunk_first": bool(base_view.route.get("force_trunk_first"))
            or bool(overlay_view.route.get("force_trunk_first")),
            "notes": list(base_view.route.get("notes", []))
            + list(overlay_view.route.get("notes", [])),
            "top_matches": list(base_view.route.get("top_matches", []))
            + list(overlay_view.route.get("top_matches", [])),
            "route_hints": list(base_view.route.get("route_hints", []))
            + list(overlay_view.route.get("route_hints", [])),
            "branch_readiness": dict(base_view.route.get("branch_readiness", {}))
            | dict(overlay_view.route.get("branch_readiness", {})),
        }
    )
    merged_isolation = _normalize_isolation(
        {
            "summary": _as_text(overlay_view.isolation.get("summary"))
            or _as_text(base_view.isolation.get("summary")),
            "surface": _as_text(overlay_view.isolation.get("surface"))
            or _as_text(base_view.isolation.get("surface")),
            "visibility": _as_text(overlay_view.isolation.get("visibility"))
            or _as_text(base_view.isolation.get("visibility")),
            "adapter": _as_text(overlay_view.isolation.get("adapter"))
            or _as_text(base_view.isolation.get("adapter")),
            "workspace_dir": _as_text(overlay_view.isolation.get("workspace_dir"))
            or _as_text(base_view.isolation.get("workspace_dir")),
            "cwd": _as_text(overlay_view.isolation.get("cwd"))
            or _as_text(base_view.isolation.get("cwd")),
            "worktree_dir": _as_text(overlay_view.isolation.get("worktree_dir"))
            or _as_text(base_view.isolation.get("worktree_dir")),
            "repo_root": _as_text(overlay_view.isolation.get("repo_root"))
            or _as_text(base_view.isolation.get("repo_root")),
            "remote_target": _as_text(overlay_view.isolation.get("remote_target"))
            or _as_text(base_view.isolation.get("remote_target")),
            "allows_writes": bool(base_view.isolation.get("allows_writes"))
            or bool(overlay_view.isolation.get("allows_writes")),
            "allows_code_execution": bool(base_view.isolation.get("allows_code_execution"))
            or bool(overlay_view.isolation.get("allows_code_execution")),
            "supports_execution": bool(base_view.isolation.get("supports_execution"))
            or bool(overlay_view.isolation.get("supports_execution")),
            "multi_agent_ready": bool(base_view.isolation.get("multi_agent_ready"))
            or bool(overlay_view.isolation.get("multi_agent_ready")),
            "delegation_ready": bool(base_view.isolation.get("delegation_ready"))
            or bool(overlay_view.isolation.get("delegation_ready")),
            "isolation_ready": bool(base_view.isolation.get("isolation_ready", True))
            and bool(overlay_view.isolation.get("isolation_ready", True)),
            "permission_ready": bool(base_view.isolation.get("permission_ready", True))
            and bool(overlay_view.isolation.get("permission_ready", True)),
            "workspace_ready": bool(base_view.isolation.get("workspace_ready", True))
            and bool(overlay_view.isolation.get("workspace_ready", True)),
            "artifact_ownership_ready": bool(base_view.isolation.get("artifact_ownership_ready", True))
            and bool(overlay_view.isolation.get("artifact_ownership_ready", True)),
            "recovery_ready": bool(base_view.isolation.get("recovery_ready", True))
            and bool(overlay_view.isolation.get("recovery_ready", True)),
            "permission_scope": _as_text(overlay_view.isolation.get("permission_scope"))
            or _as_text(base_view.isolation.get("permission_scope")),
            "artifact_scope": _as_text(overlay_view.isolation.get("artifact_scope"))
            or _as_text(base_view.isolation.get("artifact_scope")),
            "artifact_owner": _as_text(overlay_view.isolation.get("artifact_owner"))
            or _as_text(base_view.isolation.get("artifact_owner")),
            "owner_run_id": _as_text(overlay_view.isolation.get("owner_run_id"))
            or _as_text(base_view.isolation.get("owner_run_id")),
            "memory_scope": _as_text(overlay_view.isolation.get("memory_scope"))
            or _as_text(base_view.isolation.get("memory_scope")),
            "audit_scope": _as_text(overlay_view.isolation.get("audit_scope"))
            or _as_text(base_view.isolation.get("audit_scope")),
            "writable_domains": list(base_view.isolation.get("writable_domains", []))
            + list(overlay_view.isolation.get("writable_domains", [])),
            "readable_domains": list(base_view.isolation.get("readable_domains", []))
            + list(overlay_view.isolation.get("readable_domains", [])),
            "dependency_chain": list(base_view.isolation.get("dependency_chain", []))
            + list(overlay_view.isolation.get("dependency_chain", [])),
            "labels": list(base_view.isolation.get("labels", []))
            + list(overlay_view.isolation.get("labels", [])),
            "notes": list(base_view.isolation.get("notes", []))
            + list(overlay_view.isolation.get("notes", [])),
        }
    )
    merged_team_memory = _normalize_team_memory(
        {
            "summary": _as_text(overlay_view.team_memory.get("summary"))
            or _as_text(base_view.team_memory.get("summary")),
            "team_key": _as_text(overlay_view.team_memory.get("team_key"))
            or _as_text(base_view.team_memory.get("team_key")),
            "owner_session_key": _as_text(overlay_view.team_memory.get("owner_session_key"))
            or _as_text(base_view.team_memory.get("owner_session_key")),
            "owner_thread_id": _as_text(overlay_view.team_memory.get("owner_thread_id"))
            or _as_text(base_view.team_memory.get("owner_thread_id")),
            "participant_agents": list(base_view.team_memory.get("participant_agents", []))
            + list(overlay_view.team_memory.get("participant_agents", [])),
            "recent_notes": list(base_view.team_memory.get("recent_notes", []))
            + list(overlay_view.team_memory.get("recent_notes", [])),
            "recent_runs": list(base_view.team_memory.get("recent_runs", []))
            + list(overlay_view.team_memory.get("recent_runs", [])),
            "note_count": max(
                int(base_view.team_memory.get("note_count", 0) or 0),
                int(overlay_view.team_memory.get("note_count", 0) or 0),
            ),
            "active_run_count": max(
                int(base_view.team_memory.get("active_run_count", 0) or 0),
                int(overlay_view.team_memory.get("active_run_count", 0) or 0),
            ),
            "shared_memory_ready": bool(base_view.team_memory.get("shared_memory_ready"))
            or bool(overlay_view.team_memory.get("shared_memory_ready")),
            "run_status_counts": dict(base_view.team_memory.get("run_status_counts", {}))
            | dict(overlay_view.team_memory.get("run_status_counts", {})),
            "last_note": _as_text(overlay_view.team_memory.get("last_note"))
            or _as_text(base_view.team_memory.get("last_note")),
            "last_updated_at": overlay_view.team_memory.get("last_updated_at")
            or base_view.team_memory.get("last_updated_at"),
        }
    )

    return ProjectedRuntimeView(
        system_context=merged_system,
        session=merged_session,
        workspace=merged_workspace,
        tasks=merged_tasks,
        permission=merged_permission,
        settings=merged_settings,
        capability=merged_capability,
        context_hygiene=merged_context_hygiene,
        hooks=merged_hooks,
        route=merged_route,
        isolation=merged_isolation,
        team_memory=merged_team_memory,
    )


__all__ = ["merge_projected_runtime_views"]
