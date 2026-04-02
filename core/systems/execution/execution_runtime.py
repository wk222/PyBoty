"""Code-execution runtime service for execution-loop tools."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from typing import Any

from .execution_analysis import analyze_javascript_error, analyze_python_error
from .execution_workspace import create_exec_script, resolve_workspace_path


class ExecutionRuntime:
    """Run short Python or JavaScript snippets inside the workspace."""

    def __init__(self, workspace_dir: str = "workspace"):
        self.workspace_dir = workspace_dir

    def run_code(
        self,
        *,
        code: str,
        language: str = "python",
        timeout: int = 15,
        cwd: str = "",
    ) -> dict[str, Any]:
        timeout = min(timeout, 30)
        work_dir = resolve_workspace_path(self.workspace_dir, cwd)
        if work_dir is None:
            return {"success": False, "error": "工作目录不在 workspace 内"}

        os.makedirs(work_dir, exist_ok=True)
        normalized_language = language.lower()
        if normalized_language in ("python", "py"):
            return self._exec_python(code=code, timeout=timeout, cwd=work_dir)
        if normalized_language in ("javascript", "js", "node"):
            return self._exec_javascript(code=code, timeout=timeout, cwd=work_dir)
        return {"success": False, "error": f"不支持的语言: {language}"}

    def _exec_python(self, *, code: str, timeout: int, cwd: str) -> dict[str, Any]:
        script_path = create_exec_script(code, ".py", workspace_dir=self.workspace_dir)

        try:
            result = subprocess.run(
                [sys.executable, str(script_path)],
                capture_output=True,
                text=False,
                timeout=timeout,
                cwd=cwd,
                env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
            )
            
            stdout_str = result.stdout.decode("utf-8", errors="replace") if result.stdout else ""
            stderr_str = result.stderr.decode("utf-8", errors="replace") if result.stderr else ""

            output: dict[str, Any] = {
                "success": result.returncode == 0,
                "stdout": stdout_str[-3000:],
                "stderr": stderr_str[-3000:],
                "returncode": result.returncode,
            }

            if result.returncode != 0 and stderr_str:
                output["error_analysis"] = analyze_python_error(stderr_str)

            return output
        except subprocess.TimeoutExpired:
            return {
                "success": False,
                "error": f"执行超时 ({timeout}s)",
                "suggestion": "代码可能存在无限循环或耗时操作，请检查循环条件和 I/O 操作",
            }
        finally:
            self._remove_script(script_path)

    def _exec_javascript(self, *, code: str, timeout: int, cwd: str) -> dict[str, Any]:
        script_path = create_exec_script(code, ".js", workspace_dir=self.workspace_dir)

        try:
            result = subprocess.run(
                ["node", str(script_path)],
                capture_output=True,
                text=False,
                timeout=timeout,
                cwd=cwd,
            )

            stdout_str = result.stdout.decode("utf-8", errors="replace") if result.stdout else ""
            stderr_str = result.stderr.decode("utf-8", errors="replace") if result.stderr else ""

            output: dict[str, Any] = {
                "success": result.returncode == 0,
                "stdout": stdout_str[-3000:],
                "stderr": stderr_str[-3000:],
                "returncode": result.returncode,
            }

            if result.returncode != 0 and stderr_str:
                output["error_analysis"] = analyze_javascript_error(stderr_str)

            return output
        except subprocess.TimeoutExpired:
            return {
                "success": False,
                "error": f"执行超时 ({timeout}s)",
                "suggestion": "代码可能存在无限循环",
            }
        except FileNotFoundError:
            return {
                "success": False,
                "error": "Node.js 未安装",
                "suggestion": "需要安装 Node.js 才能执行 JavaScript",
            }
        finally:
            self._remove_script(script_path)

    @staticmethod
    def dumps(payload: dict[str, Any]) -> str:
        return json.dumps(payload, ensure_ascii=False, indent=2)

    @staticmethod
    def _remove_script(script_path) -> None:
        try:
            script_path.unlink()
        except OSError:
            pass
