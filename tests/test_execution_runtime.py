from __future__ import annotations

from core.execution_runtime import ExecutionRuntime


def test_execution_runtime_blocks_workspace_escape(temp_paths):
    runtime = ExecutionRuntime(workspace_dir=str(temp_paths.workspace_dir))

    result = runtime.run_code(code="print('hi')", cwd="../outside")

    assert result["success"] is False
    assert result["error"] == "工作目录不在 workspace 内"


def test_execution_runtime_returns_structured_python_success(temp_paths):
    temp_paths.workspace_dir.mkdir(parents=True, exist_ok=True)
    runtime = ExecutionRuntime(workspace_dir=str(temp_paths.workspace_dir))

    result = runtime.run_code(code="print('hello runtime')", timeout=5)

    assert result["success"] is True
    assert "hello runtime" in result["stdout"]
