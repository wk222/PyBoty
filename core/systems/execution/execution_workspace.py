"""Workspace and filesystem helpers for execution-loop tools."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

PREVIEWABLE_EXTENSIONS = frozenset({".py", ".js", ".html", ".css", ".json", ".md", ".yaml", ".yml", ".txt"})
IGNORED_SCAN_ENTRIES = frozenset({"node_modules", "__pycache__", ".git", ".tools_workspace", "venv"})
IGNORED_STATS_ENTRIES = frozenset({"node_modules", "__pycache__", ".git"})


def resolve_workspace_path(workspace_dir: str, relative_path: str = "") -> str | None:
    """Resolve a path inside the workspace and reject escapes."""
    workspace_root = os.path.realpath(workspace_dir)
    candidate = os.path.join(workspace_dir, relative_path) if relative_path else workspace_dir
    resolved = os.path.realpath(candidate)
    try:
        if os.path.commonpath([resolved, workspace_root]) != workspace_root:
            return None
    except ValueError:
        return None
    return resolved


def create_exec_script(code: str, suffix: str, *, workspace_dir: str) -> Path:
    """Write a temporary script into the shared execution workspace."""
    workspace_root = Path(workspace_dir).resolve()
    tools_workspace = workspace_root.parent / ".tools_workspace" / "exec_loop"
    tools_workspace.mkdir(parents=True, exist_ok=True)
    script_path = tools_workspace / f"exec_{os.getpid()}_{len(code)}{suffix}"
    script_path.write_text(code, encoding="utf-8")
    return script_path


def build_scan_tree(
    dir_path: str,
    *,
    max_depth: int,
    current_depth: int,
    include_content: bool,
) -> list[dict[str, Any]]:
    """Build a bounded directory tree for workspace scanning."""
    if current_depth >= max_depth:
        return [{"type": "truncated", "message": f"... (深度限制 {max_depth})"}]

    items: list[dict[str, Any]] = []
    try:
        entries = sorted(os.scandir(dir_path), key=lambda entry: (not entry.is_dir(), entry.name))
        for entry in entries:
            if entry.name.startswith(".") and entry.name not in {".env"}:
                continue
            if entry.name in IGNORED_SCAN_ENTRIES:
                continue

            if entry.is_dir():
                children = build_scan_tree(
                    entry.path,
                    max_depth=max_depth,
                    current_depth=current_depth + 1,
                    include_content=include_content,
                )
                items.append({"name": entry.name, "type": "dir", "children": children})
                continue

            if not entry.is_file():
                continue

            info: dict[str, Any] = {
                "name": entry.name,
                "type": "file",
                "size": entry.stat().st_size,
            }
            if include_content and entry.stat().st_size < 2000:
                ext = os.path.splitext(entry.name)[1].lower()
                if ext in PREVIEWABLE_EXTENSIONS:
                    try:
                        info["preview"] = Path(entry.path).read_text(encoding="utf-8")[:500]
                    except Exception:
                        pass
            items.append(info)
    except PermissionError:
        items.append({"type": "error", "message": "权限不足"})

    return items


def collect_scan_stats(
    dir_path: str,
    *,
    stats: dict[str, Any],
    max_depth: int,
    current_depth: int,
) -> None:
    """Collect bounded file statistics for workspace scanning."""
    if current_depth >= max_depth:
        return

    try:
        for entry in os.scandir(dir_path):
            if entry.name.startswith(".") or entry.name in IGNORED_STATS_ENTRIES:
                continue
            if entry.is_dir():
                stats["total_dirs"] += 1
                collect_scan_stats(
                    entry.path,
                    stats=stats,
                    max_depth=max_depth,
                    current_depth=current_depth + 1,
                )
                continue
            if not entry.is_file():
                continue
            stats["total_files"] += 1
            size = entry.stat().st_size
            stats["total_size"] += size
            ext = os.path.splitext(entry.name)[1].lower() or "(no ext)"
            stats["by_extension"][ext] = stats["by_extension"].get(ext, 0) + 1
    except PermissionError:
        return
