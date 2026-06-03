"""Session artifact helpers backed by the canonical projected runtime view."""

from __future__ import annotations

from typing import Any

from core.systems.context.projected_runtime_view import (
    ProjectedRuntimeView,
    build_runtime_task_section,
    build_projected_runtime_view,
    coerce_projected_runtime_view,
    extract_projected_runtime_view,
    merge_projected_runtime_views,
    render_projected_runtime_view,
)


def _as_text(value: Any) -> str:
    return str(value or "").strip()


def _tail(items: list[Any], limit: int) -> list[Any]:
    if limit <= 0:
        return []
    return list(items[-limit:])


def _recent_tool_runs_from_timeline(timeline: list[Any], *, limit: int = 8) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for raw in timeline:
        if not isinstance(raw, dict):
            continue
        if _as_text(raw.get("kind")) != "tool_run":
            continue
        title = _as_text(raw.get("title"))
        if not title:
            continue
        result.append(
            {
                "title": title,
                "status": _as_text(raw.get("status")),
                "source": _as_text(raw.get("source")),
                "run_id": _as_text(raw.get("run_id")),
                "preview": _as_text(raw.get("preview")),
                "timestamp": raw.get("timestamp", 0),
                "kind": "tool_run",
            }
        )
    return _tail(result, limit)


def _recent_file_views_from_timeline(timeline: list[Any], *, limit: int = 24) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for raw in timeline:
        if not isinstance(raw, dict) or _as_text(raw.get("kind")) != "file_view":
            continue
        metadata = dict(raw.get("metadata", {})) if isinstance(raw.get("metadata"), dict) else {}
        path = _as_text(metadata.get("path")) or _as_text(raw.get("title"))
        if not path:
            continue
        result.append(
            {
                "timestamp": raw.get("timestamp", 0),
                "path": path,
                "offset": int(metadata.get("offset", 0) or 0),
                "limit": int(metadata.get("limit", 0) or 0),
                "is_partial_view": bool(metadata.get("is_partial_view")),
                "view_kind": _as_text(metadata.get("view_kind")) or "full",
                "tool_name": _as_text(metadata.get("tool_name")) or "read_file",
                "source": _as_text(raw.get("source")),
                "preview": _as_text(raw.get("preview")),
                "visible_chars": int(metadata.get("visible_chars", 0) or 0),
                "content_hash": _as_text(metadata.get("content_hash")),
                "view_hash": _as_text(metadata.get("view_hash")),
            }
        )
    return _tail(result, limit)


def _persisted_runtime_view_from_record(record: Any, kernel: Any) -> ProjectedRuntimeView | None:
    runtime_view_payload = dict(getattr(kernel, "runtime_view", {}) or {})
    return extract_projected_runtime_view(runtime_view_payload)


def runtime_view_from_resume_dict(artifacts: dict[str, Any] | ProjectedRuntimeView | None) -> ProjectedRuntimeView | None:
    if isinstance(artifacts, ProjectedRuntimeView):
        return artifacts
    if not isinstance(artifacts, dict):
        return None
    payload = dict(artifacts.get("projected_runtime_view", {})) if isinstance(artifacts.get("projected_runtime_view"), dict) else {}
    if not payload:
        return None
    if isinstance(artifacts.get("system_context"), dict):
        payload["system_context"] = dict(artifacts.get("system_context", {}))
    return coerce_projected_runtime_view(payload)


def _workspace_section_from_record(record: Any, persisted_view: ProjectedRuntimeView | None = None) -> dict[str, Any]:
    workspace_projection: dict[str, Any] = dict(persisted_view.workspace) if persisted_view is not None else {}
    recent_views = list(workspace_projection.get("recent_views", []))
    if not recent_views:
        recent_views = _recent_file_views_from_timeline(list(getattr(record, "timeline", []) or []))
    if recent_views:
        workspace_projection["recent_views"] = recent_views[-24:]
    if not workspace_projection.get("recent_paths"):
        workspace_projection["recent_paths"] = [
            _as_text(item.get("path"))
            for item in recent_views
            if isinstance(item, dict) and _as_text(item.get("path"))
        ][-24:]
    notebook = (
        dict(workspace_projection.get("file_view_notebook", {}))
        if isinstance(workspace_projection.get("file_view_notebook"), dict)
        else {}
    )
    if notebook and not workspace_projection.get("notebook_summary"):
        workspace_projection["notebook_summary"] = _as_text(notebook.get("summary"))
    return workspace_projection


def _latest_compaction_boundary(record: Any, persisted_view: ProjectedRuntimeView | None = None) -> dict[str, Any]:
    if persisted_view is None:
        return {}
    boundary = (
        dict(persisted_view.context_hygiene.get("latest_boundary", {}))
        if isinstance(persisted_view.context_hygiene.get("latest_boundary"), dict)
        else {}
    )
    if boundary:
        return boundary
    boundaries = list(persisted_view.context_hygiene.get("boundaries", []))
    for raw in reversed(boundaries):
        if isinstance(raw, dict) and raw:
            return dict(raw)
    return {}


def _session_notes_from_record(record: Any, persisted_view: ProjectedRuntimeView | None = None) -> str:
    if persisted_view is not None:
        notebook = _as_text(persisted_view.session.get("session_notebook_summary"))
        if notebook:
            return notebook
        session_notebook = (
            dict(persisted_view.session.get("notebook", {}))
            if isinstance(persisted_view.session.get("notebook"), dict)
            else {}
        )
        session_summary = _as_text(session_notebook.get("summary"))
        if session_summary:
            return session_summary
        hook_notes = [_as_text(item) for item in persisted_view.hooks.get("notes", []) if _as_text(item)]
        if hook_notes:
            return "\n".join(hook_notes)
    return ""


def _system_context_from_record(
    record: Any,
    kernel: Any,
    persisted_view: ProjectedRuntimeView | None = None,
) -> dict[str, Any]:
    boundary = {}
    if persisted_view is not None and isinstance(persisted_view.system_context.get("latest_compaction_boundary"), dict):
        boundary = dict(persisted_view.system_context.get("latest_compaction_boundary", {}))
    if not boundary:
        boundary = _latest_compaction_boundary(record, persisted_view)
    return {
        "thread_id": _as_text(persisted_view.system_context.get("thread_id") if persisted_view is not None else "")
        or _as_text(getattr(record, "thread_id", ""))
        or _as_text(getattr(record, "session_key", ""))
        or "default",
        "primary_mode": _as_text(persisted_view.system_context.get("primary_mode") if persisted_view is not None else "")
        or _as_text(getattr(record, "primary_mode", ""))
        or "assistant",
        "working_summary": _as_text(persisted_view.system_context.get("working_summary") if persisted_view is not None else "")
        or _as_text(getattr(record, "working_summary", "")),
        "latest_compaction_boundary": boundary,
        "prompt_injection": _as_text(
            persisted_view.system_context.get("prompt_injection") if persisted_view is not None else ""
        ),
        "artifact_version": int(getattr(kernel, "artifact_version", 0) or 0),
    }


def render_runtime_view_context(projected_runtime_view: dict[str, Any] | ProjectedRuntimeView | None) -> str:
    """Render the canonical runtime view into compact prompt context."""
    view = extract_projected_runtime_view(projected_runtime_view)
    return render_projected_runtime_view(view)


def render_resume_dict_context(artifacts: dict[str, Any]) -> str:
    """Render wrapper artifacts by first extracting the canonical runtime view."""
    return render_runtime_view_context(runtime_view_from_resume_dict(artifacts))


def compile_runtime_resume_view(
    projected_runtime_view: dict[str, Any] | ProjectedRuntimeView | None,
    *,
    artifact_version: int | None = None,
    session_key: str = "",
) -> dict[str, Any] | None:
    view = coerce_projected_runtime_view(projected_runtime_view)
    if view is None:
        return None
    artifacts = view.to_resume_dict()
    if artifact_version is not None:
        artifacts["system_context"]["artifact_version"] = int(artifact_version or 0)
        artifacts["artifact_version"] = artifacts["system_context"]["artifact_version"]
    if _as_text(session_key):
        artifacts["system_context"]["session_key"] = _as_text(session_key)
        artifacts["session_key"] = artifacts["system_context"]["session_key"]
    return artifacts


def merge_session_runtime_view(
    base: dict[str, Any] | None,
    overlay: dict[str, Any] | None,
) -> dict[str, Any] | None:
    merged = merge_projected_runtime_views(
        runtime_view_from_resume_dict(base),
        runtime_view_from_resume_dict(overlay),
    )
    return merged.to_resume_dict() if merged is not None else None


def compile_session_runtime_view(record: Any, kernel: Any) -> dict[str, Any]:
    """Compile persisted session state into the canonical runtime view."""
    persisted_view = _persisted_runtime_view_from_record(record, kernel)
    system_context = _system_context_from_record(record, kernel, persisted_view)
    timeline = list(getattr(record, "timeline", []) or [])
    latest_boundary = dict(system_context.get("latest_compaction_boundary", {}))
    boundaries = list(persisted_view.context_hygiene.get("boundaries", [])) if persisted_view is not None else []
    derived_context_hygiene = {
        "summary_active": bool(latest_boundary),
        "current_cutoff_index": 0,
        "last_microcompact_count": int(
            dict(latest_boundary.get("metadata", {})).get("microcompact_count", 0)
            if isinstance(latest_boundary.get("metadata", {}), dict)
            else 0
        ),
        "history_snip_count": len(boundaries),
        "latest_boundary": latest_boundary,
    }
    overlay_view = build_projected_runtime_view(
        thread_id=system_context["thread_id"],
        root_mode=system_context["primary_mode"],
        system_context=system_context,
        session={
            "session_notebook_summary": _session_notes_from_record(record, persisted_view),
            "working_summary": system_context["working_summary"],
            "compaction_summary": _as_text(latest_boundary.get("summary")),
        },
        workspace=_workspace_section_from_record(record, persisted_view),
        tasks=build_runtime_task_section(
            recent_tool_runs=_recent_tool_runs_from_timeline(timeline),
            latest_compaction_boundary=latest_boundary,
        ),
        context_hygiene=derived_context_hygiene,
    )
    view = merge_projected_runtime_views(persisted_view, overlay_view) or overlay_view
    artifacts = compile_runtime_resume_view(
        view,
        artifact_version=int(getattr(kernel, "artifact_version", 0) or 0),
        session_key=_as_text(getattr(record, "session_key", "")),
    ) or view.to_resume_dict()
    artifacts["user_context"] = {
        "last_user_message": _as_text(getattr(record, "last_user_message", "")),
        "last_assistant_message": _as_text(getattr(record, "last_assistant_message", "")),
        "last_message_preview": _as_text(getattr(record, "last_message_preview", "")),
        "title": _as_text(getattr(record, "title", "")),
        "status": _as_text(getattr(record, "status", "")),
        "message_count": int(getattr(record, "message_count", 0) or 0),
    }
    return artifacts


__all__ = [
    "ProjectedRuntimeView",
    "compile_runtime_resume_view",
    "compile_session_runtime_view",
    "coerce_projected_runtime_view",
    "merge_session_runtime_view",
    "render_resume_dict_context",
    "render_runtime_view_context",
    "runtime_view_from_resume_dict",
]
