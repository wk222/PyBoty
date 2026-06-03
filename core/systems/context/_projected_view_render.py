"""Render a :class:`ProjectedRuntimeView` into a markdown prompt fragment."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .projected_runtime_view import ProjectedRuntimeView


def _as_text(value: Any) -> str:
    return str(value or "").strip()


def render_projected_runtime_view(payload: "dict[str, Any] | ProjectedRuntimeView | None") -> str:
    """Render the canonical view payload into a human-readable markdown block."""
    from .projected_runtime_view import coerce_projected_runtime_view

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
            lines.append(
                f"- {_as_text(item.get('id'))}: {_as_text(item.get('content'))} "
                f"[{_as_text(item.get('status'))}]"
            )
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
            reason = _as_text(item.get("reason"))
            verdict_part = _as_text(item.get("verdict"))
            entry = f"- {_as_text(item.get('tool_name'))}: {verdict_part}"
            if reason:
                entry += f" ({reason})"
            lines.append(entry)
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
            lines.append(f"- {label}: {path}{suffix}" if path else f"- {label}{suffix}")
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

    boundary = (
        dict(view.system_context.get("latest_compaction_boundary", {}))
        if isinstance(view.system_context.get("latest_compaction_boundary"), dict)
        else {}
    )
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
            lines.append(
                f"- last microcompact count: {int(context_hygiene.get('last_microcompact_count', 0) or 0)}"
            )
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
                lines.append(
                    f"- {_as_text(item.get('phase'))}: {int(item.get('handler_count', 0) or 0)} hooks"
                )
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
                f"- {_as_text(isolation.get('visibility')) or 'unknown'} / "
                f"{_as_text(isolation.get('adapter')) or 'unknown'}"
            )
        if isinstance(isolation.get("multi_agent_ready"), bool):
            lines.append(
                f"- multi-agent ready: {'yes' if bool(isolation.get('multi_agent_ready')) else 'no'}"
            )
        if len(lines) > 1:
            parts.append("\n".join(lines))

    return "\n\n".join(part for part in parts if part).strip()


__all__ = ["render_projected_runtime_view"]
