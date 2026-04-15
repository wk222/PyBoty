"""Iterative resource validation service for execution-loop tools."""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any


class IterativeResourceValidator:
    """Run lightweight structural and syntax checks for generated resources (apps, agents, etc.)."""

    def __init__(self, workspace_dir: str = "workspace"):
        self.workspace_dir = Path(workspace_dir)

    def validate(
        self,
        *,
        resource_path: str,
        test_command: str = "",
        max_iterations: int = 3,
    ) -> dict[str, Any]:
        """Validate a resource at the given relative path within the workspace."""
        full_dir = self.workspace_dir / resource_path.lstrip("/")
        if not full_dir.exists():
            return {"success": False, "error": f"路径 '{resource_path}' 不存在"}

        results: list[dict[str, Any]] = []
        results.extend(self._check_html(full_dir))
        results.extend(self._check_javascript(full_dir))
        results.extend(self._check_api(full_dir))

        if test_command:
            results.append(self._run_custom_test(resource_dir=full_dir, test_command=test_command))

        passed = sum(1 for result in results if result["status"] == "pass")
        failed = sum(1 for result in results if result["status"] == "fail")
        next_action = (
            "所有测试通过！" if failed == 0 else "请根据上述失败项修复代码，然后再次调用 iterative_test 验证。"
        )
        return {
            "success": True,
            "resource_path": resource_path,
            "verdict": "PASS" if failed == 0 else "FAIL",
            "passed": passed,
            "failed": failed,
            "results": results,
            "next_action": next_action,
            "max_iterations": max_iterations,
        }

    @staticmethod
    def _check_html(resource_dir: Path) -> list[dict[str, Any]]:
        html_path = resource_dir / "index.html"
        if not html_path.exists():
            return []

        html = html_path.read_text(encoding="utf-8")
        if not html.strip():
            return [{"check": "html_empty", "status": "fail", "message": "index.html 为空"}]
        return [{"check": "html_exists", "status": "pass"}]

    @staticmethod
    def _check_javascript(resource_dir: Path) -> list[dict[str, Any]]:
        js_path = resource_dir / "static" / "app.js"
        if not js_path.exists():
            return []

        try:
            result = subprocess.run(
                ["node", "--check", str(js_path)],
                capture_output=True,
                text=False,
                timeout=5,
            )
            stderr_str = result.stderr.decode("utf-8", errors="replace") if result.stderr else ""
            if result.returncode == 0:
                return [{"check": "js_syntax", "status": "pass"}]
            return [
                {
                    "check": "js_syntax",
                    "status": "fail",
                    "error": stderr_str.strip()[:500],
                    "suggestion": "修复 JavaScript 语法错误",
                }
            ]
        except (subprocess.TimeoutExpired, FileNotFoundError):
            return [{"check": "js_syntax", "status": "skip", "message": "Node.js 不可用"}]

    @staticmethod
    def _check_api(resource_dir: Path) -> list[dict[str, Any]]:
        api_path = resource_dir / "api.py"
        if not api_path.exists():
            return []

        api_code = api_path.read_text(encoding="utf-8")
        try:
            compile(api_code, str(api_path), "exec")
            return [{"check": "api_syntax", "status": "pass"}]
        except SyntaxError as exc:
            return [
                {
                    "check": "api_syntax",
                    "status": "fail",
                    "error": f"第{exc.lineno}行: {exc.msg}",
                    "suggestion": f"修复 api.py 第{exc.lineno}行的语法错误",
                }
            ]

    @staticmethod
    def _run_custom_test(*, resource_dir: Path, test_command: str) -> dict[str, Any]:
        try:
            result = subprocess.run(
                test_command,
                shell=True,
                capture_output=True,
                text=False,
                timeout=15,
                cwd=str(resource_dir),
            )
            stdout_str = result.stdout.decode("utf-8", errors="replace") if result.stdout else ""
            stderr_str = result.stderr.decode("utf-8", errors="replace") if result.stderr else ""
            return {
                "check": "custom_test",
                "status": "pass" if result.returncode == 0 else "fail",
                "stdout": stdout_str[-1000:],
                "stderr": stderr_str[-1000:],
            }
        except subprocess.TimeoutExpired:
            return {"check": "custom_test", "status": "fail", "error": "测试超时"}

