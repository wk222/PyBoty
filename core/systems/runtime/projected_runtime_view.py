"""Canonical projected runtime view for prompt, compact, resume, and routing."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

_CANONICAL_SECTION_KEYS = (
    "session",
    "workspace",
    "tasks",
    "permission",
    "settings",
    "capability",
    "context_hygiene",
    "hooks",
    "route",
    "isolation",
    "team_memory",
)


def _as_text(value: Any) -> str:
    return str(value or "").strip()


def _tail(items: list[Any], limit: int) -> list[Any]:
    if limit <= 0:
        return []
    return list(items[-limit:])


def _dedupe_strings(items: list[Any], *, limit: int) -> list[str]:
    seen: set[str] = set()
    ordered: list[str] = []
    for raw in items:
        value = _as_text(raw)
        if not value or value in seen:
            continue
        seen.add(value)
        ordered.append(value)
    return _tail(ordered, limit)


def _dedupe_mappings(
    items: list[Any],
    *,
    key_fields: tuple[str, ...],
    limit: int,
) -> list[dict[str, Any]]:
    merged: dict[str, dict[str, Any]] = {}
    order: list[str] = []
    for raw in items:
        if not isinstance(raw, dict):
            continue
        key = ""
        for field_name in key_fields:
            value = _as_text(raw.get(field_name))
            if value:
                key = value
                break
        if not key:
            key = "|".join(_as_text(raw.get(name)) for name in key_fields)
        if not key:
            continue
        merged[key] = dict(raw)
        if key in order:
            order.remove(key)
        order.append(key)
    return [merged[key] for key in _tail(order, limit)]


def _normalize_entry_list(items: list[Any], *, limit: int = 24) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    for raw in items:
        if isinstance(raw, dict):
            normalized.append(dict(raw))
    return _tail(normalized, limit)


def _normalize_notebook_block(raw: dict[str, Any] | None, *, limit: int = 16) -> dict[str, Any]:
    data = dict(raw or {})
    return {
        "summary": _as_text(data.get("summary")),
        "entries": _normalize_entry_list(list(data.get("entries", [])), limit=limit),
        "last_updated_at": data.get("last_updated_at"),
    }


def _merge_notebook_block(
    base: dict[str, Any] | None,
    overlay: dict[str, Any] | None,
    *,
    limit: int = 16,
) -> dict[str, Any]:
    base_block = _normalize_notebook_block(base, limit=limit)
    overlay_block = _normalize_notebook_block(overlay, limit=limit)
    merged_entries = _dedupe_mappings(
        list(base_block.get("entries", [])) + list(overlay_block.get("entries", [])),
        key_fields=("timestamp", "kind", "summary"),
        limit=limit,
    )
    return {
        "summary": _as_text(overlay_block.get("summary")) or _as_text(base_block.get("summary")),
        "entries": merged_entries,
        "last_updated_at": overlay_block.get("last_updated_at") or base_block.get("last_updated_at"),
    }


def _normalize_workspace_view(projection: dict[str, Any] | None, *, limit: int = 8) -> dict[str, Any]:
    data = dict(projection or {})
    recent_views = _dedupe_mappings(
        list(data.get("recent_views", [])),
        key_fields=("view_hash", "path"),
        limit=limit,
    )
    recent_paths = _dedupe_strings(
        list(data.get("recent_paths", [])) + [item.get("path", "") for item in recent_views],
        limit=limit,
    )
    return {
        "recent_paths": recent_paths,
        "recent_views": recent_views,
        "path_labels": [_as_text(path).split("/")[-1].split("\\")[-1] for path in recent_paths],
        "view_hashes": _dedupe_strings([item.get("view_hash", "") for item in recent_views], limit=limit),
        "partial_views": sum(1 for item in recent_views if bool(item.get("is_partial_view"))),
        "notebook_summary": _as_text(data.get("notebook_summary")),
        "stats": dict(data.get("stats", {})) if isinstance(data.get("stats"), dict) else {},
        "file_view_notebook": _normalize_notebook_block(data.get("file_view_notebook"), limit=16),
        "last_updated_at": data.get("last_updated_at"),
        "summary": _as_text(data.get("summary")),
        "last_note": _as_text(data.get("last_note")),
        "last_note_type": _as_text(data.get("last_note_type")),
        "entries": _normalize_entry_list(list(data.get("entries", []))),
    }


def _task_items_from_projection(projection: dict[str, Any] | None, *, limit: int = 8) -> list[dict[str, Any]]:
    items = list((projection or {}).get("items", []))
    normalized: list[dict[str, Any]] = []
    for raw in items:
        if not isinstance(raw, dict):
            continue
        task_id = _as_text(raw.get("id"))
        if not task_id:
            continue
        normalized.append(
            {
                "id": task_id,
                "content": _as_text(raw.get("content")),
                "status": _as_text(raw.get("status")) or "pending",
                "source": _as_text(raw.get("source")) or "task_projection",
                "updated_at": raw.get("updated_at", 0),
                "lifecycle": _as_text(raw.get("lifecycle")) or "foreground",
                "surface": _as_text(raw.get("surface")) or "chat",
            }
        )
    return _dedupe_mappings(normalized, key_fields=("id",), limit=limit)


def _tool_activities_from_runs(runs: list[Any], *, limit: int = 8) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    for raw in runs:
        if not isinstance(raw, dict):
            continue
        title = _as_text(raw.get("title"))
        if not title:
            continue
        activity_id = _as_text(raw.get("run_id")) or f"tool:{title}:{_as_text(raw.get('status'))}:{raw.get('timestamp', 0)}"
        normalized.append(
            {
                "activity_id": activity_id,
                "kind": _as_text(raw.get("kind")) or "tool_run",
                "title": title,
                "status": _as_text(raw.get("status")),
                "source": _as_text(raw.get("source")) or "tool_projection",
                "preview": _as_text(raw.get("preview")),
                "task_id": _as_text(raw.get("task_id")),
                "timestamp": raw.get("timestamp", 0),
                "lifecycle": _as_text(raw.get("lifecycle")) or "foreground",
            }
        )
    return _dedupe_mappings(normalized, key_fields=("activity_id",), limit=limit)


def _governance_activities_from_permission(permission_projection: dict[str, Any] | None, *, limit: int = 8) -> list[dict[str, Any]]:
    events = list((permission_projection or {}).get("recent_events", []))
    normalized: list[dict[str, Any]] = []
    for raw in events:
        if not isinstance(raw, dict):
            continue
        action = _as_text(raw.get("action"))
        if not action:
            continue
        target = _as_text(raw.get("tool_name")) or _as_text(raw.get("mode"))
        title = f"governance:{action}"
        if target:
            title = f"{title} {target}"
        normalized.append(
            {
                "activity_id": _as_text(raw.get("event_id")) or f"gov:{title}:{raw.get('timestamp', 0)}",
                "kind": "governance",
                "title": title,
                "status": _as_text(raw.get("verdict")) or _as_text(raw.get("mode")),
                "source": _as_text(raw.get("source")) or "permission_projection",
                "preview": _as_text(raw.get("reason")),
                "task_id": "",
                "timestamp": raw.get("timestamp", 0),
                "lifecycle": "foreground",
            }
        )
    return _dedupe_mappings(normalized, key_fields=("activity_id",), limit=limit)


def _compaction_activities(boundary: dict[str, Any] | None) -> list[dict[str, Any]]:
    if not isinstance(boundary, dict) or not boundary:
        return []
    return [
        {
            "activity_id": _as_text(boundary.get("boundary_id")) or f"compaction:{boundary.get('timestamp', 0)}",
            "kind": "compaction",
            "title": _as_text(boundary.get("reason")) or "conversation_compaction",
            "status": "summarized",
            "source": _as_text(boundary.get("source")) or "compaction",
            "preview": _as_text(boundary.get("summary")),
            "task_id": "",
            "timestamp": boundary.get("timestamp", 0),
            "lifecycle": "resume",
        }
    ]


def _normalize_task_runtime(
    projection: dict[str, Any] | None,
    *,
    task_projection: dict[str, Any] | None = None,
    recent_tool_runs: list[Any] | None = None,
    permission_projection: dict[str, Any] | None = None,
    latest_compaction_boundary: dict[str, Any] | None = None,
    limit: int = 8,
) -> dict[str, Any]:
    data = dict(projection or {})
    tasks = _dedupe_mappings(
        list(data.get("tasks", [])) + _task_items_from_projection(task_projection, limit=limit),
        key_fields=("id",),
        limit=limit,
    )
    activities = _dedupe_mappings(
        list(data.get("activities", []))
        + _tool_activities_from_runs(list(recent_tool_runs or []), limit=limit)
        + _governance_activities_from_permission(permission_projection, limit=limit)
        + _compaction_activities(latest_compaction_boundary),
        key_fields=("activity_id",),
        limit=limit,
    )

    status_counts: dict[str, int] = {}
    for item in tasks:
        status = _as_text(item.get("status")) or "pending"
        status_counts[status] = status_counts.get(status, 0) + 1

    activity_counts: dict[str, int] = {}
    lifecycle_counts: dict[str, int] = {}
    for item in activities:
        kind = _as_text(item.get("kind")) or "activity"
        activity_counts[kind] = activity_counts.get(kind, 0) + 1
        lifecycle = _as_text(item.get("lifecycle")) or "foreground"
        lifecycle_counts[lifecycle] = lifecycle_counts.get(lifecycle, 0) + 1

    summary = _as_text(data.get("summary"))
    if not summary and (tasks or activities):
        active = status_counts.get("pending", 0) + status_counts.get("in_progress", 0)
        completed = status_counts.get("completed", 0)
        summary = f"{active} active tasks, {completed} completed, {len(activities)} recent activities"

    return {
        "summary": summary,
        "tasks": tasks,
        "activities": activities,
        "status_counts": status_counts,
        "activity_counts": activity_counts,
        "lifecycle_counts": lifecycle_counts,
    }


def build_runtime_task_section(
    *,
    task_runtime: dict[str, Any] | None = None,
    task_projection: dict[str, Any] | None = None,
    recent_tool_runs: list[Any] | None = None,
    permission: dict[str, Any] | None = None,
    latest_compaction_boundary: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return _normalize_task_runtime(
        task_runtime,
        task_projection=task_projection,
        recent_tool_runs=recent_tool_runs,
        permission_projection=permission,
        latest_compaction_boundary=latest_compaction_boundary,
    )


def _normalize_permission(projection: dict[str, Any] | None, *, limit: int = 8) -> dict[str, Any]:
    data = dict(projection or {})
    rules = _dedupe_mappings(list(data.get("rules", [])), key_fields=("tool_name",), limit=limit)
    recent_events = _dedupe_mappings(
        list(data.get("recent_events", [])),
        key_fields=("event_id", "action", "tool_name", "mode"),
        limit=limit,
    )
    sources = data.get("sources", {})
    return {
        "mode": _as_text(data.get("mode")) or "default",
        "summary": _as_text(data.get("summary")) or f"mode={_as_text(data.get('mode')) or 'default'}",
        "rules": rules,
        "recent_events": recent_events,
        "write_tools": list(data.get("write_tools", [])) if isinstance(data.get("write_tools"), list) else [],
        "rule_count": int(data.get("rule_count", len(rules)) or 0),
        "sources": dict(sources) if isinstance(sources, dict) else {},
    }


def _normalize_capability(projection: dict[str, Any] | None, *, limit: int = 8) -> dict[str, Any]:
    data = dict(projection or {})
    return {
        "trunk_chain": list(data.get("trunk_chain", [])) if isinstance(data.get("trunk_chain"), list) else [],
        "trunk_summary": _as_text(data.get("trunk_summary")),
        "execution_surfaces": list(data.get("execution_surfaces", []))
        if isinstance(data.get("execution_surfaces"), list)
        else [],
        "execution_summary": _as_text(data.get("execution_summary")),
        "primary_branches": list(data.get("primary_branches", []))
        if isinstance(data.get("primary_branches"), list)
        else [],
        "secondary_branches": list(data.get("secondary_branches", []))
        if isinstance(data.get("secondary_branches"), list)
        else [],
        "principles": list(data.get("principles", [])) if isinstance(data.get("principles"), list) else [],
        "route_hints": _dedupe_mappings(list(data.get("route_hints", [])), key_fields=("topic",), limit=limit),
    }


def _normalize_settings(projection: dict[str, Any] | None, *, limit: int = 8) -> dict[str, Any]:
    data = dict(projection or {})
    sources = _dedupe_mappings(
        list(data.get("sources", [])),
        key_fields=("source",),
        limit=limit,
    )
    return {
        "summary": _as_text(data.get("summary")),
        "active_sources": _dedupe_strings(list(data.get("active_sources", [])), limit=limit),
        "sources": sources,
        "paths": dict(data.get("paths", {})) if isinstance(data.get("paths"), dict) else {},
        "permission_mode": _as_text(data.get("permission_mode")) or "default",
    }


def _normalize_session(projection: dict[str, Any] | None, *, working_summary: str, compaction_summary: str) -> dict[str, Any]:
    data = dict(projection or {})
    normalized_working_summary = _as_text(data.get("working_summary")) or working_summary
    normalized_compaction_summary = _as_text(data.get("compaction_summary")) or compaction_summary
    return {
        "session_notebook_summary": _as_text(data.get("session_notebook_summary")),
        "working_summary": normalized_working_summary,
        "compaction_summary": normalized_compaction_summary,
        "notebook": _normalize_notebook_block(data.get("notebook"), limit=12),
        "tool_transcript": _normalize_notebook_block(data.get("tool_transcript"), limit=16),
        "compaction": dict(data.get("compaction", {})) if isinstance(data.get("compaction"), dict) else {},
        "summary": _as_text(data.get("summary")),
        "last_note": _as_text(data.get("last_note")),
        "last_note_type": _as_text(data.get("last_note_type")),
        "entries": _normalize_entry_list(list(data.get("entries", []))),
    }


def _normalize_context_hygiene(projection: dict[str, Any] | None) -> dict[str, Any]:
    data = dict(projection or {})
    latest_boundary = dict(data.get("latest_boundary", {})) if isinstance(data.get("latest_boundary"), dict) else {}
    boundaries = _normalize_entry_list(list(data.get("boundaries", [])), limit=16)
    history = _normalize_entry_list(list(data.get("history", [])), limit=12)
    return {
        "summary_active": bool(data.get("summary_active")),
        "current_cutoff_index": int(data.get("current_cutoff_index", 0) or 0),
        "last_microcompact_count": int(data.get("last_microcompact_count", 0) or 0),
        "history_snip_count": int(data.get("history_snip_count", 0) or 0),
        "latest_boundary": latest_boundary,
        "boundaries": boundaries,
        "history": history,
        "resume_scrubbed_events": int(data.get("resume_scrubbed_events", 0) or 0),
        "compacted_tool_events": int(data.get("compacted_tool_events", 0) or 0),
        "tool_notebook_entries": int(data.get("tool_notebook_entries", 0) or 0),
        "compacted_file_views": int(data.get("compacted_file_views", 0) or 0),
        "file_view_notebook_entries": int(data.get("file_view_notebook_entries", 0) or 0),
        "compacted_notes": int(data.get("compacted_notes", 0) or 0),
        "compacted_timeline_events": int(data.get("compacted_timeline_events", 0) or 0),
        "microcompacted_previews": int(data.get("microcompacted_previews", 0) or 0),
        "microcompacted_metadata": int(data.get("microcompacted_metadata", 0) or 0),
        "notebook_entries": int(data.get("notebook_entries", 0) or 0),
        "last_reason": _as_text(data.get("last_reason")),
        "last_compacted_at": data.get("last_compacted_at"),
        "garden_suggestions": list(data.get("garden_suggestions", [])),
    }


def _normalize_hooks(projection: dict[str, Any] | None, *, limit: int = 8) -> dict[str, Any]:
    data = dict(projection or {})
    phase_counts = _dedupe_mappings(
        list(data.get("phase_counts", [])),
        key_fields=("phase",),
        limit=limit,
    )
    recent_runs = _dedupe_mappings(
        list(data.get("recent_runs", [])),
        key_fields=("phase", "hook_name", "timestamp"),
        limit=limit,
    )
    return {
        "summary": _as_text(data.get("summary")),
        "active_phases": _dedupe_strings(list(data.get("active_phases", [])), limit=limit),
        "phase_counts": phase_counts,
        "recent_runs": recent_runs,
        "notes": _dedupe_strings(list(data.get("notes", [])), limit=limit),
        "session_tags": _dedupe_strings(list(data.get("session_tags", [])), limit=limit),
    }


def _normalize_route(projection: dict[str, Any] | None, *, limit: int = 8) -> dict[str, Any]:
    data = dict(projection or {})
    recommended = dict(data.get("recommended", {})) if isinstance(data.get("recommended"), dict) else {}
    raw_branch_readiness = data.get("branch_readiness", {})
    branch_readiness: dict[str, dict[str, Any]] = {}
    if isinstance(raw_branch_readiness, dict):
        for branch_name, payload in raw_branch_readiness.items():
            normalized_branch = _as_text(branch_name)
            if not normalized_branch or not isinstance(payload, dict):
                continue
            branch_readiness[normalized_branch] = {
                "ready": bool(payload.get("ready")),
                "reasons": _dedupe_strings(list(payload.get("reasons", [])), limit=limit),
            }
    return {
        "summary": _as_text(
            recommended.get("summary")
            or data.get("summary")
        ),
        "recommended": recommended,
        "prefer_slots": _dedupe_strings(list(data.get("prefer_slots", [])), limit=limit),
        "avoid_slots": _dedupe_strings(list(data.get("avoid_slots", [])), limit=limit),
        "avoid_top_levels": _dedupe_strings(list(data.get("avoid_top_levels", [])), limit=limit),
        "force_trunk_first": bool(data.get("force_trunk_first")),
        "notes": _dedupe_strings(list(data.get("notes", [])), limit=limit),
        "top_matches": _dedupe_mappings(list(data.get("top_matches", [])), key_fields=("name",), limit=limit),
        "route_hints": _dedupe_mappings(list(data.get("route_hints", [])), key_fields=("topic",), limit=limit),
        "branch_readiness": branch_readiness,
    }


def _normalize_isolation(projection: dict[str, Any] | None, *, limit: int = 8) -> dict[str, Any]:
    data = dict(projection or {})
    return {
        "summary": _as_text(data.get("summary")),
        "surface": _as_text(data.get("surface")) or "single_agent_runtime",
        "agent_name": _as_text(data.get("agent_name")),
        "thread_id": _as_text(data.get("thread_id")),
        "parent_thread_id": _as_text(data.get("parent_thread_id")),
        "owner_thread_id": _as_text(data.get("owner_thread_id")),
        "owner_session_key": _as_text(data.get("owner_session_key")),
        "depth": int(data.get("depth", 0) or 0),
        "visibility": _as_text(data.get("visibility")),
        "adapter": _as_text(data.get("adapter")),
        "backend": _as_text(data.get("backend")) or "local",
        "workspace_dir": _as_text(data.get("workspace_dir")),
        "cwd": _as_text(data.get("cwd")),
        "worktree_dir": _as_text(data.get("worktree_dir")),
        "repo_root": _as_text(data.get("repo_root")),
        "remote_target": _as_text(data.get("remote_target")),
        "allows_writes": bool(data.get("allows_writes")),
        "allows_code_execution": bool(data.get("allows_code_execution")),
        "supports_execution": bool(data.get("supports_execution")),
        "multi_agent_ready": bool(data.get("multi_agent_ready")),
        "delegation_ready": bool(data.get("delegation_ready")),
        "isolation_ready": bool(data.get("isolation_ready", True)),
        "permission_ready": bool(data.get("permission_ready", True)),
        "workspace_ready": bool(data.get("workspace_ready", True)),
        "artifact_ownership_ready": bool(data.get("artifact_ownership_ready", True)),
        "recovery_ready": bool(data.get("recovery_ready", True)),
        "permission_scope": _as_text(data.get("permission_scope")),
        "artifact_scope": _as_text(data.get("artifact_scope")),
        "artifact_owner": _as_text(data.get("artifact_owner")),
        "owner_run_id": _as_text(data.get("owner_run_id")),
        "memory_scope": _as_text(data.get("memory_scope")),
        "audit_scope": _as_text(data.get("audit_scope")),
        "tool_scope": _dedupe_strings(list(data.get("tool_scope", [])), limit=limit),
        "writable_domains": _dedupe_strings(list(data.get("writable_domains", [])), limit=limit),
        "readable_domains": _dedupe_strings(list(data.get("readable_domains", [])), limit=limit),
        "requires_strict_isolation": bool(data.get("requires_strict_isolation")),
        "requires_workspace_visibility": bool(data.get("requires_workspace_visibility")),
        "dependency_chain": _dedupe_strings(list(data.get("dependency_chain", [])), limit=limit),
        "labels": _dedupe_strings(list(data.get("labels", [])), limit=limit),
        "notes": _dedupe_strings(list(data.get("notes", [])), limit=limit),
    }


def _normalize_team_memory(projection: dict[str, Any] | None, *, limit: int = 8) -> dict[str, Any]:
    data = dict(projection or {})
    recent_notes = _dedupe_mappings(
        list(data.get("recent_notes", [])),
        key_fields=("note_id", "run_id", "timestamp"),
        limit=limit,
    )
    recent_runs = _dedupe_mappings(
        list(data.get("recent_runs", [])),
        key_fields=("run_id",),
        limit=limit,
    )
    return {
        "summary": _as_text(data.get("summary")),
        "team_key": _as_text(data.get("team_key")),
        "owner_session_key": _as_text(data.get("owner_session_key")),
        "owner_thread_id": _as_text(data.get("owner_thread_id")),
        "participant_agents": _dedupe_strings(list(data.get("participant_agents", [])), limit=limit),
        "recent_notes": recent_notes,
        "recent_runs": recent_runs,
        "note_count": int(data.get("note_count", len(recent_notes)) or 0),
        "active_run_count": int(data.get("active_run_count", 0) or 0),
        "shared_memory_ready": bool(data.get("shared_memory_ready")),
        "run_status_counts": (
            dict(data.get("run_status_counts", {}))
            if isinstance(data.get("run_status_counts"), dict)
            else {}
        ),
        "last_note": _as_text(data.get("last_note")),
        "last_updated_at": data.get("last_updated_at"),
    }


@dataclass(slots=True)
class ProjectedRuntimeView:
    system_context: dict[str, Any] = field(default_factory=dict)
    session: dict[str, Any] = field(default_factory=dict)
    workspace: dict[str, Any] = field(default_factory=dict)
    tasks: dict[str, Any] = field(default_factory=dict)
    permission: dict[str, Any] = field(default_factory=dict)
    settings: dict[str, Any] = field(default_factory=dict)
    capability: dict[str, Any] = field(default_factory=dict)
    context_hygiene: dict[str, Any] = field(default_factory=dict)
    hooks: dict[str, Any] = field(default_factory=dict)
    route: dict[str, Any] = field(default_factory=dict)
    isolation: dict[str, Any] = field(default_factory=dict)
    team_memory: dict[str, Any] = field(default_factory=dict)

    def to_payload(self) -> dict[str, Any]:
        return {
            "system_context": dict(self.system_context),
            "session": dict(self.session),
            "workspace": dict(self.workspace),
            "tasks": dict(self.tasks),
            "permission": dict(self.permission),
            "settings": dict(self.settings),
            "capability": dict(self.capability),
            "context_hygiene": dict(self.context_hygiene),
            "hooks": dict(self.hooks),
            "route": dict(self.route),
            "isolation": dict(self.isolation),
            "team_memory": dict(self.team_memory),
        }

    def to_resume_dict(self) -> dict[str, Any]:
        payload = self.to_payload()
        artifacts = {
            "system_context": dict(self.system_context),
            "user_context": {},
            "projected_runtime_view": payload,
        }
        artifact_version = self.system_context.get("artifact_version")
        session_key = self.system_context.get("session_key")
        if artifact_version is not None:
            artifacts["artifact_version"] = artifact_version
        if session_key:
            artifacts["session_key"] = session_key
        return artifacts


def build_projected_runtime_view(
    *,
    thread_id: str,
    root_mode: str,
    system_context: dict[str, Any] | None = None,
    session: dict[str, Any] | None = None,
    workspace: dict[str, Any] | None = None,
    tasks: dict[str, Any] | None = None,
    permission: dict[str, Any] | None = None,
    settings: dict[str, Any] | None = None,
    capability: dict[str, Any] | None = None,
    context_hygiene: dict[str, Any] | None = None,
    hooks: dict[str, Any] | None = None,
    route: dict[str, Any] | None = None,
    isolation: dict[str, Any] | None = None,
    team_memory: dict[str, Any] | None = None,
) -> ProjectedRuntimeView:
    resolved_system = dict(system_context or {})
    latest_compaction_boundary = (
        dict(resolved_system.get("latest_compaction_boundary", {}))
        if isinstance(resolved_system.get("latest_compaction_boundary"), dict)
        else {}
    )
    raw_session = dict(session or {})
    compaction_summary = _as_text(raw_session.get("compaction_summary")) or _as_text(
        latest_compaction_boundary.get("summary")
    )
    working_summary = (
        _as_text(resolved_system.get("working_summary"))
        or _as_text(raw_session.get("working_summary"))
        or compaction_summary
    )
    normalized_session = _normalize_session(
        raw_session,
        working_summary=working_summary,
        compaction_summary=compaction_summary,
    )
    resolved_system = {
        "thread_id": _as_text(resolved_system.get("thread_id")) or _as_text(thread_id) or "default",
        "primary_mode": _as_text(resolved_system.get("primary_mode")) or _as_text(root_mode) or "assistant",
        "working_summary": working_summary,
        "latest_compaction_boundary": latest_compaction_boundary,
        "prompt_injection": _as_text(resolved_system.get("prompt_injection")),
        **{
            key: value
            for key, value in resolved_system.items()
            if key not in {"thread_id", "primary_mode", "working_summary", "latest_compaction_boundary", "prompt_injection"}
        },
    }
    normalized_workspace = _normalize_workspace_view(workspace)
    normalized_permission = _normalize_permission(permission)
    normalized_settings = _normalize_settings(settings)
    normalized_tasks = _normalize_task_runtime(
        tasks,
        permission_projection=normalized_permission,
        latest_compaction_boundary=latest_compaction_boundary,
    )
    normalized_capability = _normalize_capability(capability)
    normalized_context_hygiene = _normalize_context_hygiene(
        context_hygiene
        or {
            "summary_active": bool(compaction_summary or latest_compaction_boundary),
            "current_cutoff_index": 0,
            "last_microcompact_count": int(
                dict(latest_compaction_boundary.get("metadata", {})).get("microcompact_count", 0)
                if isinstance(latest_compaction_boundary.get("metadata", {}), dict)
                else 0
            ),
            "history_snip_count": 1 if latest_compaction_boundary else 0,
            "latest_boundary": latest_compaction_boundary,
        }
    )
    normalized_hooks = _normalize_hooks(hooks)
    normalized_route = _normalize_route(route)
    normalized_isolation = _normalize_isolation(isolation)
    normalized_team_memory = _normalize_team_memory(team_memory)
    return ProjectedRuntimeView(
        system_context=resolved_system,
        session=normalized_session,
        workspace=normalized_workspace,
        tasks=normalized_tasks,
        permission=normalized_permission,
        settings=normalized_settings,
        capability=normalized_capability,
        context_hygiene=normalized_context_hygiene,
        hooks=normalized_hooks,
        route=normalized_route,
        isolation=normalized_isolation,
        team_memory=normalized_team_memory,
    )


def coerce_projected_runtime_view(payload: dict[str, Any] | ProjectedRuntimeView | None) -> ProjectedRuntimeView | None:
    if payload is None:
        return None
    if isinstance(payload, ProjectedRuntimeView):
        return payload
    if not isinstance(payload, dict):
        return None
    if not any(key in payload for key in _CANONICAL_SECTION_KEYS):
        return None

    system_context = dict(payload.get("system_context", {})) if isinstance(payload.get("system_context"), dict) else {}
    session = dict(payload.get("session", {})) if isinstance(payload.get("session"), dict) else {}
    workspace = dict(payload.get("workspace", {})) if isinstance(payload.get("workspace"), dict) else {}
    tasks = dict(payload.get("tasks", {})) if isinstance(payload.get("tasks"), dict) else {}
    permission = dict(payload.get("permission", {})) if isinstance(payload.get("permission"), dict) else {}
    settings = dict(payload.get("settings", {})) if isinstance(payload.get("settings"), dict) else {}
    capability = dict(payload.get("capability", {})) if isinstance(payload.get("capability"), dict) else {}
    context_hygiene = dict(payload.get("context_hygiene", {})) if isinstance(payload.get("context_hygiene"), dict) else {}
    hooks = dict(payload.get("hooks", {})) if isinstance(payload.get("hooks"), dict) else {}
    route = dict(payload.get("route", {})) if isinstance(payload.get("route"), dict) else {}
    isolation = dict(payload.get("isolation", {})) if isinstance(payload.get("isolation"), dict) else {}
    team_memory = dict(payload.get("team_memory", {})) if isinstance(payload.get("team_memory"), dict) else {}

    return build_projected_runtime_view(
        thread_id=_as_text(system_context.get("thread_id")) or "default",
        root_mode=_as_text(system_context.get("primary_mode")) or "assistant",
        system_context=system_context,
        session=session,
        workspace=workspace,
        tasks=tasks,
        permission=permission,
        settings=settings,
        capability=capability,
        context_hygiene=context_hygiene,
        hooks=hooks,
        route=route,
        isolation=isolation,
        team_memory=team_memory,
    )


def extract_projected_runtime_view(payload: dict[str, Any] | ProjectedRuntimeView | None) -> ProjectedRuntimeView | None:
    return coerce_projected_runtime_view(payload)


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
    base: dict[str, Any] | ProjectedRuntimeView | None,
    overlay: dict[str, Any] | ProjectedRuntimeView | None,
) -> ProjectedRuntimeView | None:
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
            "compaction": dict(base_view.session.get("compaction", {})) | dict(overlay_view.session.get("compaction", {})),
            "summary": _as_text(overlay_view.session.get("summary")) or _as_text(base_view.session.get("summary")),
            "last_note": _as_text(overlay_view.session.get("last_note")) or _as_text(base_view.session.get("last_note")),
            "last_note_type": _as_text(overlay_view.session.get("last_note_type"))
            or _as_text(base_view.session.get("last_note_type")),
            "entries": list(base_view.session.get("entries", [])) + list(overlay_view.session.get("entries", [])),
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
            "stats": dict(base_view.workspace.get("stats", {})) | dict(overlay_view.workspace.get("stats", {})),
            "file_view_notebook": _merge_notebook_block(
                base_view.workspace.get("file_view_notebook", {}),
                overlay_view.workspace.get("file_view_notebook", {}),
                limit=16,
            ),
            "last_updated_at": overlay_view.workspace.get("last_updated_at") or base_view.workspace.get("last_updated_at"),
            "summary": _as_text(overlay_view.workspace.get("summary")) or _as_text(base_view.workspace.get("summary")),
            "last_note": _as_text(overlay_view.workspace.get("last_note"))
            or _as_text(base_view.workspace.get("last_note")),
            "last_note_type": _as_text(overlay_view.workspace.get("last_note_type"))
            or _as_text(base_view.workspace.get("last_note_type")),
            "entries": list(base_view.workspace.get("entries", [])) + list(overlay_view.workspace.get("entries", [])),
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
                overlay_permission_mode if overlay_has_permission_data else _as_text(base_view.permission.get("mode"))
            ),
            "summary": _as_text(overlay_view.permission.get("summary"))
            or _as_text(base_view.permission.get("summary")),
            "rules": list(base_view.permission.get("rules", [])) + list(overlay_view.permission.get("rules", [])),
            "recent_events": list(base_view.permission.get("recent_events", []))
            + list(overlay_view.permission.get("recent_events", [])),
            "write_tools": list(base_view.permission.get("write_tools", []))
            + list(overlay_view.permission.get("write_tools", [])),
            "sources": dict(base_view.permission.get("sources", {})) | dict(overlay_view.permission.get("sources", {})),
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
            "summary": _as_text(overlay_view.settings.get("summary")) or _as_text(base_view.settings.get("summary")),
            "active_sources": list(base_view.settings.get("active_sources", []))
            + list(overlay_view.settings.get("active_sources", [])),
            "sources": list(base_view.settings.get("sources", [])) + list(overlay_view.settings.get("sources", [])),
            "paths": dict(base_view.settings.get("paths", {})) | dict(overlay_view.settings.get("paths", {})),
            "permission_mode": (
                overlay_settings_mode
                if overlay_has_settings_data
                else _as_text(base_view.settings.get("permission_mode"))
            ),
        }
    )
    merged_tasks = _normalize_task_runtime(
        {
            "summary": _as_text(overlay_view.tasks.get("summary")) or _as_text(base_view.tasks.get("summary")),
            "tasks": list(base_view.tasks.get("tasks", [])) + list(overlay_view.tasks.get("tasks", [])),
            "activities": list(base_view.tasks.get("activities", [])) + list(overlay_view.tasks.get("activities", [])),
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
            "last_microcompact_count": int(overlay_view.context_hygiene.get("last_microcompact_count", 0) or 0)
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
            "summary": _as_text(overlay_view.hooks.get("summary")) or _as_text(base_view.hooks.get("summary")),
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
            "summary": _as_text(overlay_view.route.get("summary")) or _as_text(base_view.route.get("summary")),
            "recommended": dict(base_view.route.get("recommended", {})) | dict(overlay_view.route.get("recommended", {})),
            "prefer_slots": list(base_view.route.get("prefer_slots", []))
            + list(overlay_view.route.get("prefer_slots", [])),
            "avoid_slots": list(base_view.route.get("avoid_slots", []))
            + list(overlay_view.route.get("avoid_slots", [])),
            "avoid_top_levels": list(base_view.route.get("avoid_top_levels", []))
            + list(overlay_view.route.get("avoid_top_levels", [])),
            "force_trunk_first": bool(base_view.route.get("force_trunk_first"))
            or bool(overlay_view.route.get("force_trunk_first")),
            "notes": list(base_view.route.get("notes", [])) + list(overlay_view.route.get("notes", [])),
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
            "labels": list(base_view.isolation.get("labels", [])) + list(overlay_view.isolation.get("labels", [])),
            "notes": list(base_view.isolation.get("notes", [])) + list(overlay_view.isolation.get("notes", [])),
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


def render_projected_runtime_view(payload: dict[str, Any] | ProjectedRuntimeView | None) -> str:
    view = coerce_projected_runtime_view(payload)
    if view is None:
        return ""

    parts: list[str] = []
    session_notes = _as_text(view.session.get("session_notebook_summary"))
    if session_notes:
        parts.append(f"## Session Notebook\n{session_notes}")

    task_summary = _as_text(view.tasks.get("summary"))
    tasks = list(view.tasks.get("tasks", []))
    if task_summary or tasks:
        lines = ["## Task Runtime"]
        if task_summary:
            lines.append(task_summary)
        for item in tasks[:8]:
            lines.append(f"- {_as_text(item.get('id'))}: {_as_text(item.get('content'))} [{_as_text(item.get('status'))}]")
        parts.append("\n".join(lines))

    activities = list(view.tasks.get("activities", []))
    if activities:
        lines = ["## Recent Activity"]
        for item in activities[:8]:
            status = _as_text(item.get("status"))
            suffix = f" ({status})" if status else ""
            lines.append(f"- {_as_text(item.get('kind'))}: {_as_text(item.get('title'))}{suffix}")
        parts.append("\n".join(lines))

    recent_views = list(view.workspace.get("recent_views", []))
    if recent_views:
        lines = ["## Workspace Views"]
        for item in recent_views[:8]:
            label = _as_text(item.get("path"))
            view_kind = _as_text(item.get("view_kind")) or "view"
            line_range = _as_text(item.get("line_range"))
            suffix = f" {line_range}" if line_range else ""
            lines.append(f"- {label} ({view_kind}{suffix})")
        parts.append("\n".join(lines))

    permission_mode = _as_text(view.permission.get("mode"))
    if permission_mode:
        lines = ["## Permission Control", f"mode: {permission_mode}"]
        for item in list(view.permission.get("rules", []))[:8]:
            lines.append(
                f"- {_as_text(item.get('tool_name'))}: {_as_text(item.get('verdict'))}"
                + (f" ({_as_text(item.get('reason'))})" if _as_text(item.get("reason")) else "")
            )
        parts.append("\n".join(lines))

    settings_summary = _as_text(view.settings.get("summary"))
    settings_sources = list(view.settings.get("sources", []))
    if settings_summary or settings_sources:
        lines = ["## Trusted Settings"]
        if settings_summary:
            lines.append(settings_summary)
        for item in settings_sources[:8]:
            if not isinstance(item, dict):
                continue
            label = _as_text(item.get("source"))
            path = _as_text(item.get("path"))
            entry_count = int(item.get("entry_count", 0) or 0)
            suffix = f" ({entry_count} entries)" if entry_count else ""
            if path:
                lines.append(f"- {label}: {path}{suffix}")
            else:
                lines.append(f"- {label}{suffix}")
        parts.append("\n".join(lines))

    capability_summary = _as_text(view.capability.get("trunk_summary"))
    route_hints = list(view.capability.get("route_hints", []))
    primary_branches = list(view.capability.get("primary_branches", []))
    if capability_summary or primary_branches or route_hints:
        lines = ["## Capability Tree"]
        if capability_summary:
            lines.append(capability_summary)
        execution_summary = _as_text(view.capability.get("execution_summary"))
        if execution_summary:
            lines.append(execution_summary)
        for branch in primary_branches[:4]:
            if isinstance(branch, dict):
                lines.append(f"- branch: {_as_text(branch.get('label'))}")
        for hint in route_hints[:8]:
            if isinstance(hint, dict):
                lines.append(f"- route:{_as_text(hint.get('topic'))} -> {_as_text(hint.get('hint'))}")
        parts.append("\n".join(lines))

    boundary = dict(view.system_context.get("latest_compaction_boundary", {})) if isinstance(view.system_context.get("latest_compaction_boundary"), dict) else {}
    if boundary:
        parts.append(
            "\n".join(
                [
                    "## Latest Compaction",
                    _as_text(boundary.get("reason")) or "conversation_compaction",
                    _as_text(boundary.get("summary")),
                ]
            )
        )

    prompt_injection = _as_text(view.system_context.get("prompt_injection"))
    if prompt_injection:
        parts.append(f"## Prompt Injection\n{prompt_injection}")

    context_hygiene = dict(view.context_hygiene)
    if context_hygiene:
        lines = ["## Context Hygiene"]
        if context_hygiene.get("summary_active"):
            lines.append("- summary active")
        if int(context_hygiene.get("history_snip_count", 0) or 0):
            lines.append(f"- history snips: {int(context_hygiene.get('history_snip_count', 0) or 0)}")
        if int(context_hygiene.get("last_microcompact_count", 0) or 0):
            lines.append(f"- last microcompact count: {int(context_hygiene.get('last_microcompact_count', 0) or 0)}")
        if int(context_hygiene.get("current_cutoff_index", 0) or 0):
            lines.append(f"- cutoff index: {int(context_hygiene.get('current_cutoff_index', 0) or 0)}")
        
        suggestions = list(context_hygiene.get("garden_suggestions", []))
        if suggestions:
            lines.append("### Garden Update Suggestions (Pending Knowledge)")
            for s in suggestions[:5]:
                lines.append(f"- {s}")
                
        if len(lines) > 1:
            parts.append("\n".join(lines))

    team_memory = dict(view.team_memory)
    if team_memory:
        lines = ["## Team Memory"]
        summary = _as_text(team_memory.get("summary"))
        if summary:
            lines.append(summary)
        for item in list(team_memory.get("recent_notes", []))[:6]:
            if not isinstance(item, dict):
                continue
            author = _as_text(item.get("agent_name")) or "agent"
            note = _as_text(item.get("note"))
            if note:
                lines.append(f"- {author}: {note}")
        if len(lines) > 1:
            parts.append("\n".join(lines))

    hooks = dict(view.hooks)
    if hooks:
        lines = ["## Hooks Runtime"]
        summary = _as_text(hooks.get("summary"))
        if summary:
            lines.append(summary)
        for item in list(hooks.get("phase_counts", []))[:8]:
            if isinstance(item, dict):
                lines.append(f"- {_as_text(item.get('phase'))}: {int(item.get('handler_count', 0) or 0)} hooks")
        for tag in list(hooks.get("session_tags", []))[:4]:
            lines.append(f"- tag: {_as_text(tag)}")
        if len(lines) > 1:
            parts.append("\n".join(lines))

    route = dict(view.route)
    if route:
        lines = ["## Route Selection"]
        summary = _as_text(route.get("summary"))
        if summary:
            lines.append(summary)
        recommended = route.get("recommended", {}) if isinstance(route.get("recommended"), dict) else {}
        slot = _as_text(recommended.get("slot_label") or recommended.get("slot"))
        top_level = _as_text(recommended.get("top_level_label") or recommended.get("top_level"))
        if slot or top_level:
            lines.append(f"- recommended: {slot or 'trunk'} / {top_level or 'trunk'}")
        for note in list(route.get("notes", []))[:4]:
            lines.append(f"- note: {_as_text(note)}")
        if len(lines) > 1:
            parts.append("\n".join(lines))

    isolation = dict(view.isolation)
    if isolation:
        lines = ["## Isolation Model"]
        summary = _as_text(isolation.get("summary"))
        if summary:
            lines.append(summary)
        if _as_text(isolation.get("visibility")) or _as_text(isolation.get("adapter")):
            lines.append(
                f"- {_as_text(isolation.get('visibility')) or 'unknown'} / {_as_text(isolation.get('adapter')) or 'unknown'}"
            )
        if isinstance(isolation.get("multi_agent_ready"), bool):
            lines.append(
                f"- multi-agent ready: {'yes' if bool(isolation.get('multi_agent_ready')) else 'no'}"
            )
        if len(lines) > 1:
            parts.append("\n".join(lines))

    return "\n\n".join(part for part in parts if part).strip()
