from __future__ import annotations

import json

from core.execution_analysis import analyze_python_error
from core.execution_loop import ExecCodeTool, ScanProjectTool
from core.execution_workspace import resolve_workspace_path


def test_resolve_workspace_path_blocks_escape(temp_paths):
    assert resolve_workspace_path(str(temp_paths.workspace_dir), "../outside") is None


def test_analyze_python_error_extracts_missing_module():
    analysis = analyze_python_error("ModuleNotFoundError: No module named 'pandas'")

    assert analysis["type"] == "import_error"
    assert analysis["module"] == "pandas"


def test_exec_code_tool_returns_structured_name_error(temp_paths):
    temp_paths.workspace_dir.mkdir(parents=True, exist_ok=True)
    tool = ExecCodeTool(workspace_dir=str(temp_paths.workspace_dir))

    result = json.loads(tool._run("print(missing_name)", timeout=5))

    assert result["success"] is False
    assert result["error_analysis"]["type"] == "name_error"


def test_scan_project_tool_returns_stats_for_workspace_file(temp_paths):
    temp_paths.workspace_dir.mkdir(parents=True, exist_ok=True)
    (temp_paths.workspace_dir / "demo.txt").write_text("hello", encoding="utf-8")
    tool = ScanProjectTool(workspace_dir=str(temp_paths.workspace_dir))

    result = json.loads(tool._run())

    assert result["success"] is True
    assert result["stats"]["total_files"] == 1
    assert result["stats"]["by_extension"][".txt"] == 1


def test_scan_project_tool_blocks_path_escape(temp_paths):
    tool = ScanProjectTool(workspace_dir=str(temp_paths.workspace_dir))

    result = json.loads(tool._run(path="../outside"))

    assert result["success"] is False
    assert result["error"] == "路径不在 workspace 内"
