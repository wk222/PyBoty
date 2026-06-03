from __future__ import annotations

from core.systems.execution.execution_scanner import ProjectScanner


def test_project_scanner_returns_stats_for_workspace_files(temp_paths):
    temp_paths.workspace_dir.mkdir(parents=True, exist_ok=True)
    (temp_paths.workspace_dir / "notes.txt").write_text("hello", encoding="utf-8")
    scanner = ProjectScanner(workspace_dir=str(temp_paths.workspace_dir))

    result = scanner.scan()

    assert result["success"] is True
    assert result["stats"]["total_files"] == 1
    assert result["stats"]["by_extension"][".txt"] == 1
