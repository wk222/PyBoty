"""Project-scanning service for execution-loop tools."""

from __future__ import annotations

from typing import Any

from .execution_workspace import build_scan_tree, collect_scan_stats, resolve_workspace_path


class ProjectScanner:
    """Produce bounded workspace trees and file statistics."""

    def __init__(self, workspace_dir: str = "workspace"):
        self.workspace_dir = workspace_dir

    def scan(
        self,
        *,
        path: str = "",
        max_depth: int = 3,
        include_content: bool = False,
    ) -> dict[str, Any]:
        scan_dir = resolve_workspace_path(self.workspace_dir, path)
        if scan_dir is None:
            return {"success": False, "error": "路径不在 workspace 内"}

        from os import path as os_path

        if not os_path.exists(scan_dir):
            return {"success": False, "error": f"目录不存在: {path}"}

        tree = build_scan_tree(
            scan_dir,
            max_depth=max_depth,
            current_depth=0,
            include_content=include_content,
        )

        stats = {"total_files": 0, "total_dirs": 0, "total_size": 0, "by_extension": {}}
        collect_scan_stats(scan_dir, stats=stats, max_depth=max_depth, current_depth=0)

        return {
            "success": True,
            "root": path or self.workspace_dir,
            "tree": tree,
            "stats": stats,
        }
