"""Compiled session artifacts used by the session spine."""

from __future__ import annotations

from pathlib import Path
from typing import Any


def _typed_memory_groups(memory_layers: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for layer_name, layer in memory_layers.items():
        if not isinstance(layer, dict):
            continue
        for entry in layer.get("entries", []):
            if not isinstance(entry, dict):
                continue
            memory_type = str(entry.get("memory_type", "")).strip() or "other"
            grouped.setdefault(memory_type, []).append(
                {
                    "layer": layer_name,
                    "note": str(entry.get("note", "")).strip(),
                    "occurred_on": str(entry.get("occurred_on", "")).strip(),
                    "verified": bool(entry.get("verified")),
                }
            )
    return grouped


def _latest_tool_runs(timeline: list[dict[str, Any]], *, limit: int = 6) -> list[dict[str, Any]]:
    items = [item for item in timeline if str(item.get("kind", "")).strip() == "tool_run"]
    return items[-max(1, int(limit)) :]


def render_artifact_context(artifacts: dict[str, Any]) -> str:
    if not isinstance(artifacts, dict):
        return ""
    parts: list[str] = []

    system_ctx = artifacts.get("system_context", {})
    working_summary = str(system_ctx.get("working_summary", "")).strip()
    prompt_injection = str(system_ctx.get("prompt_injection", "")).strip()
    primary_mode = str(system_ctx.get("primary_mode", "")).strip()

    if working_summary or primary_mode:
        lines = ["## 会话工作上下文"]
        if primary_mode:
            lines.append(f"当前模式: {primary_mode}")
        if working_summary:
            lines.append(f"工作摘要: {working_summary}")
        parts.append("\n".join(lines))

    notebook = artifacts.get("session_notebook_projection", {})
    session_nb_summary = str(notebook.get("session_notebook_summary", "")).strip()
    tool_transcript_summary = str(notebook.get("tool_transcript_summary", "")).strip()
    compaction_summary = str(notebook.get("compaction_summary", "")).strip()
    if session_nb_summary or tool_transcript_summary or compaction_summary:
        lines = ["## 会话笔记本摘要"]
        if session_nb_summary:
            lines.append(session_nb_summary)
        if tool_transcript_summary:
            lines.append(f"工具记录: {tool_transcript_summary}")
        if compaction_summary:
            lines.append(f"压缩摘要: {compaction_summary}")
        parts.append("\n".join(lines))

    user_ctx = artifacts.get("user_context", {})
    context_notes = [str(n).strip() for n in user_ctx.get("context_notes", []) if str(n).strip()]
    if context_notes:
        parts.append("## 上下文备注\n" + "\n".join(f"- {note}" for note in context_notes))

    typed_memory = user_ctx.get("typed_memory", {})
    if isinstance(typed_memory, dict):
        mem_lines: list[str] = []
        for mem_type, entries in typed_memory.items():
            if not isinstance(entries, list) or not entries:
                continue
            for entry in entries[:4]:
                if not isinstance(entry, dict):
                    continue
                note = str(entry.get("note", "")).strip()
                if note:
                    mem_lines.append(f"[{mem_type}] {note}")
        if mem_lines:
            parts.append("## 持久记忆摘要\n" + "\n".join(mem_lines))

    file_proj = artifacts.get("file_view_projection", {})
    recent_paths = [str(p).strip() for p in file_proj.get("recent_paths", []) if str(p).strip()]
    if recent_paths:
        parts.append("## 近期文件视图\n" + "\n".join(f"- {p}" for p in recent_paths[:6]))

    if prompt_injection:
        parts.append(f"## 注入指令\n{prompt_injection}")

    return "\n\n".join(parts)


def compile_session_artifacts(record: Any, kernel: Any) -> dict[str, Any]:
    session_layer = record.memory_layers.get("session", {}) if isinstance(record.memory_layers, dict) else {}
    workspace_layer = record.memory_layers.get("workspace", {}) if isinstance(record.memory_layers, dict) else {}
    compaction = record.compaction_state if isinstance(record.compaction_state, dict) else {}
    boundaries = list(compaction.get("boundaries", [])) if isinstance(compaction, dict) else []
    latest_boundary = boundaries[-1] if boundaries else {}

    file_views = workspace_layer.get("file_views", {}) if isinstance(workspace_layer, dict) else {}
    file_view_recent = list(file_views.get("recent", [])) if isinstance(file_views, dict) else []
    file_view_notebook = file_views.get("notebook", {}) if isinstance(file_views, dict) else {}
    recent_paths = []
    for item in file_view_recent[-8:]:
        if not isinstance(item, dict):
            continue
        path = str(item.get("path", "")).strip()
        if path and path not in recent_paths:
            recent_paths.append(path)

    tool_transcript = session_layer.get("tool_transcript", {}) if isinstance(session_layer, dict) else {}
    notebook = session_layer.get("notebook", {}) if isinstance(session_layer, dict) else {}

    return {
        "artifact_version": int(getattr(kernel, "artifact_version", 0)),
        "system_context": {
            "session_key": record.session_key,
            "thread_id": record.thread_id,
            "primary_mode": record.primary_mode,
            "title": record.title,
            "status": record.status,
            "mode_history": list(record.mode_history),
            "source_history": list(record.source_history),
            "working_summary": record.working_summary,
            "prompt_injection": str(record.metadata.get("prompt_injection", "")).strip()
            if isinstance(record.metadata, dict)
            else "",
            "latest_compaction_boundary": latest_boundary,
        },
        "user_context": {
            "last_user_message": record.last_user_message,
            "last_assistant_message": record.last_assistant_message,
            "context_notes": list(record.context_notes),
            "typed_memory": _typed_memory_groups(record.memory_layers),
        },
        "session_notebook_projection": {
            "session_notebook_summary": str(notebook.get("summary", "")).strip() if isinstance(notebook, dict) else "",
            "tool_transcript_summary": str(tool_transcript.get("summary", "")).strip()
            if isinstance(tool_transcript, dict)
            else "",
            "compaction_summary": str(session_layer.get("compaction", {}).get("summary", "")).strip()
            if isinstance(session_layer.get("compaction", {}), dict)
            else "",
        },
        "file_view_projection": {
            "recent_paths": recent_paths,
            "recent_views": file_view_recent[-8:],
            "notebook_summary": str(file_view_notebook.get("summary", "")).strip()
            if isinstance(file_view_notebook, dict)
            else "",
            "partial_views": sum(
                1 for item in file_view_recent if isinstance(item, dict) and item.get("is_partial_view")
            ),
            "path_labels": [Path(path).name for path in recent_paths],
            "view_hashes": [
                str(item.get("view_hash", "")).strip()
                for item in file_view_recent[-8:]
                if isinstance(item, dict) and str(item.get("view_hash", "")).strip()
            ],
        },
        "tool_projection": {
            "recent_tool_runs": _latest_tool_runs(record.timeline),
            "tool_transcript_entries": list(tool_transcript.get("entries", []))[-4:]
            if isinstance(tool_transcript, dict)
            else [],
        },
        "kernel": kernel.snapshot() if hasattr(kernel, "snapshot") else {},
    }
