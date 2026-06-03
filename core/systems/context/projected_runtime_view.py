"""Canonical projected runtime view for prompt, compact, resume, and routing."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

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


from typing import Any, Callable, Protocol, runtime_checkable


@runtime_checkable
class RuntimeContext(Protocol):
    """Protocol for runtime components used during view projection."""
    thread_id: str
    root_mode: str
    workspace: Any
    workspace_view: Any
    memory: Any
    capability_bus: Any
    trusted_settings: Any
    hooks_runtime: Any
    subagent_registry: Any
    task_runtime: Any
    middleware: Any
    session_runtime: Any
    session_memory_extractor: Any | None = None


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

    @classmethod
    def from_runtime(
        cls,
        runtime: RuntimeContext,
        *,
        system_overlay: dict[str, Any] | None = None,
        session_overlay: dict[str, Any] | None = None,
        context_hygiene_overlay: dict[str, Any] | None = None,
        session_notes_provider: Callable[[], str] | None = None,
    ) -> ProjectedRuntimeView:
        """Create a ProjectedRuntimeView directly from a runtime instance with optional overlays."""
        notes_provider = session_notes_provider
        if notes_provider is None and hasattr(runtime, "session_memory_extractor") and runtime.session_memory_extractor is not None:
            if hasattr(runtime.session_memory_extractor, "get_notes"):
                notes_provider = runtime.session_memory_extractor.get_notes

        return cls.compile(
            runtime=runtime,
            system_overlay=system_overlay,
            session_overlay=session_overlay,
            context_hygiene_overlay=context_hygiene_overlay,
            session_notes_provider=notes_provider,
        )

    @classmethod
    def compile(
        cls,
        *,
        runtime: RuntimeContext,
        system_overlay: dict[str, Any] | None = None,
        session_overlay: dict[str, Any] | None = None,
        context_hygiene_overlay: dict[str, Any] | None = None,
        session_notes_provider: Callable[[], str] | None = None,
    ) -> ProjectedRuntimeView:
        """Centralized assembly of a ProjectedRuntimeView from a runtime instance."""
        _tid = runtime.thread_id
        _root_mode = runtime.root_mode
        _sys = dict(system_overlay or {})
        _sess = dict(session_overlay or {})
        _hygiene = dict(context_hygiene_overlay or {})
        
        workspace_dir = (
            getattr(runtime.workspace, "root_dir", "")
            or getattr(runtime.workspace, "_root_dir", "")
            or "."
        )
        
        session_key = ""
        if hasattr(runtime, "session_runtime") and runtime.session_runtime is not None:
            try:
                session_key = runtime.session_runtime.session_key_for_thread(_tid)
            except Exception as exc:
                logger.debug("session_key_for_thread(%s) failed: %s", _tid, exc)

        multi_agent_ready = bool(
            runtime.subagent_registry is not None
            and runtime.task_runtime is not None
            and runtime.middleware is not None
        )

        return compile_live_view(
            thread_id=_tid,
            root_mode=_root_mode,
            system_overlay=_sys,
            session_overlay=_sess,
            context_hygiene_overlay=_hygiene,
            workspace_view=runtime.workspace_view,
            capability_bus=runtime.capability_bus,
            trusted_settings=runtime.trusted_settings,
            hooks_runtime=runtime.hooks_runtime,
            subagent_registry=runtime.subagent_registry,
            task_runtime=runtime.task_runtime,
            tool_middleware=runtime.middleware,
            session_notes_provider=session_notes_provider,
            workspace_dir=workspace_dir,
            multi_agent_ready=multi_agent_ready,
            session_key=session_key,
        )

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


def compile_live_view(
    *,
    thread_id: str,
    root_mode: str,
    system_overlay: dict[str, Any],
    session_overlay: dict[str, Any],
    context_hygiene_overlay: dict[str, Any],
    workspace_view: Any = None,
    capability_bus: Any = None,
    trusted_settings: Any = None,
    hooks_runtime: Any = None,
    subagent_registry: Any = None,
    task_runtime: Any = None,
    tool_middleware: Any = None,
    session_notes_provider: Callable[[], str] | None = None,
    workspace_dir: str = ".",
    multi_agent_ready: bool = False,
    session_key: str = "",
) -> ProjectedRuntimeView:
    """Centralized assembly of a ProjectedRuntimeView from raw runtime components."""
    latest_boundary = dict(system_overlay.get("latest_compaction_boundary", {}))

    # 1. Workspace
    workspace_projection = {}
    if workspace_view is not None and hasattr(workspace_view, "build_projection"):
        try:
            workspace_projection = workspace_view.build_projection(limit=8)
        except Exception as exc:
            logger.debug("compile_live_view: workspace projection failed: %s", exc)

    # 2. Capability
    capability_projection = {}
    if capability_bus is not None and hasattr(capability_bus, "get_tree_projection"):
        try:
            from core.systems.capability.capability_tree import build_capability_tree_resume_projection
            capability_projection = build_capability_tree_resume_projection(capability_bus.get_tree_projection())
        except Exception as exc:
            logger.debug("compile_live_view: capability projection failed: %s", exc)

    # 3. Settings
    settings_projection = {}
    if trusted_settings is not None and hasattr(trusted_settings, "build_projection"):
        try:
            settings_projection = trusted_settings.build_projection()
        except Exception as exc:
            logger.debug("compile_live_view: settings projection failed: %s", exc)

    # 4. Hooks
    hooks_projection = {}
    if hooks_runtime is not None and hasattr(hooks_runtime, "build_projection"):
        try:
            hooks_projection = hooks_runtime.build_projection()
        except Exception as exc:
            logger.debug("compile_live_view: hooks projection failed: %s", exc)

    # 5. Isolation
    from core.systems.runtime.subagent_isolation import build_root_isolation_projection
    isolation_projection = build_root_isolation_projection(
        workspace_dir=workspace_dir,
        root_mode=root_mode,
        multi_agent_ready=multi_agent_ready,
        thread_id=thread_id,
        session_key=session_key,
        hooks_runtime=hooks_runtime,
    )

    # 6. Tool / Permission
    recent_tool_runs = []
    permission_projection = {}
    if tool_middleware is not None and hasattr(tool_middleware, "get_control_snapshot"):
        try:
            snapshot = tool_middleware.get_control_snapshot()
            observability = snapshot.get("observability", {}) if isinstance(snapshot, dict) else {}
            recent_events = observability.get("recent_events", []) if isinstance(observability, dict) else []
            permission_projection = snapshot.get("permission", {}) if isinstance(snapshot, dict) else {}
            
            # If snapshot has settings, prefer those as they might be more 'live'
            snapshot_settings = snapshot.get("settings", {})
            if isinstance(snapshot_settings, dict) and snapshot_settings:
                settings_projection = snapshot_settings

            for event in recent_events[-6:]:
                if not isinstance(event, dict): continue
                tool_name = str(event.get("tool_name", "")).strip()
                if not tool_name: continue
                status = "completed"
                if event.get("requires_approval"): status = "approval_required"
                elif not event.get("allowed", True): status = "blocked"
                recent_tool_runs.append({
                    "title": tool_name,
                    "status": status,
                    "source": "tool_control",
                    "run_id": str(event.get("tool_call_id", "")).strip(),
                    "preview": str(event.get("args_preview", "")).strip(),
                    "timestamp": event.get("timestamp"),
                })
        except Exception as exc:
            logger.debug("compile_live_view: tool_middleware snapshot failed: %s", exc)

    # 7. Team Memory
    team_memory_projection = {}
    if subagent_registry is not None and hasattr(subagent_registry, "build_team_memory_projection"):
        try:
            team_memory_projection = subagent_registry.build_team_memory_projection(
                team_key=session_key or thread_id,
                owner_session_key=session_key,
                owner_thread_id=thread_id,
            )
        except Exception as exc:
            logger.debug("compile_live_view: team_memory projection failed: %s", exc)

    # 8. Tasks
    task_projection = {}
    if task_runtime is not None:
        try:
            if recent_tool_runs:
                task_runtime.ingest_tool_runs(recent_tool_runs, source="tool_control")
            if isinstance(permission_projection, dict):
                task_runtime.ingest_permission_events(
                    list(permission_projection.get("recent_events", [])),
                    source="permission_projection",
                )
            if latest_boundary:
                task_runtime.record_compaction_boundary(dict(latest_boundary))
            task_projection = task_runtime.build_projection() or {}
        except Exception as exc:
            logger.debug("compile_live_view: tasks projection failed: %s", exc)

    # Build intermediate view for routing
    def _compose_with_overrides(route_section=None, hooks_override=None):
        return build_projected_runtime_view(
            thread_id=thread_id,
            root_mode=root_mode,
            system_context={
                "thread_id": thread_id,
                "primary_mode": root_mode,
                "working_summary": str(system_overlay.get("working_summary", "")).strip(),
                "latest_compaction_boundary": latest_boundary,
                "prompt_injection": str(system_overlay.get("prompt_injection", "")).strip(),
            },
            session={
                "session_notebook_summary": session_notes_provider() if session_notes_provider else "",
                "working_summary": str(session_overlay.get("working_summary", "")).strip(),
                "compaction_summary": str(session_overlay.get("compaction_summary", "")).strip(),
            },
            workspace=workspace_projection,
            tasks=build_runtime_task_section(
                task_runtime=task_projection,
                recent_tool_runs=recent_tool_runs,
                permission=permission_projection,
                latest_compaction_boundary=latest_boundary,
            ),
            permission=permission_projection,
            settings=settings_projection,
            capability=capability_projection,
            context_hygiene=context_hygiene_overlay,
            hooks=hooks_override or hooks_projection,
            route=route_section or {},
            isolation=isolation_projection,
            team_memory=team_memory_projection,
        )

    # 9. Routing
    route_projection = {}
    preliminary_view_payload = _compose_with_overrides().to_payload()
    if hooks_runtime is not None and hasattr(hooks_runtime, "run_phase"):
        try:
            from core.systems.runtime.hooks_runtime import HookPhase
            route_projection = hooks_runtime.run_phase(
                HookPhase.ROUTE_SELECTION,
                {"query": "", "provides": "", "projected_runtime_view": preliminary_view_payload}
            )
        except Exception as exc:
            logger.debug("compile_live_view: route hook phase failed: %s", exc)

    if capability_bus is not None and hasattr(capability_bus, "get_route_projection"):
        try:
            bus_route = capability_bus.get_route_projection(
                query="", provides="", projected_runtime_view=preliminary_view_payload
            )
            route_projection = {
                **dict(bus_route or {}),
                "prefer_slots": list(route_projection.get("prefer_slots", [])),
                "avoid_slots": list(route_projection.get("avoid_slots", [])),
                "notes": list(route_projection.get("notes", [])),
            }
        except Exception as exc:
            logger.debug("compile_live_view: capability_bus route projection failed: %s", exc)

    # 10. Session Bookkeeping
    if hooks_runtime is not None and hasattr(hooks_runtime, "run_phase"):
        try:
            from core.systems.runtime.hooks_runtime import HookPhase
            mid_view = _compose_with_overrides(route_section=route_projection).to_payload()
            bookkeeping = hooks_runtime.run_phase(
                HookPhase.SESSION_BOOKKEEPING,
                {"projected_runtime_view": mid_view}
            )
            if bookkeeping.get("notes") or bookkeeping.get("session_tags"):
                hooks_projection = {
                    **hooks_projection,
                    "notes": list(bookkeeping.get("notes", [])),
                    "session_tags": list(bookkeeping.get("session_tags", [])),
                }
        except Exception as exc:
            logger.debug("compile_live_view: session bookkeeping hook failed: %s", exc)

    return _compose_with_overrides(route_section=route_projection, hooks_override=hooks_projection)


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


from ._projected_view_merge import merge_projected_runtime_views  # noqa: E402
from ._projected_view_render import render_projected_runtime_view  # noqa: E402

__all__ = [
    "ProjectedRuntimeView",
    "RuntimeContext",
    "build_projected_runtime_view",
    "build_runtime_task_section",
    "coerce_projected_runtime_view",
    "extract_projected_runtime_view",
    "merge_projected_runtime_views",
    "render_projected_runtime_view",
]

